from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import re
from typing import Any, Protocol

import httpx
from firebase_admin import firestore
from google.cloud.firestore_v1 import Client

from app.schemas.matches import MatchDetail
from app.schemas.media import StoredMatchMedia


logger = logging.getLogger(__name__)
FIFA_HIGHLIGHTS_URL = "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026"


class MediaRepository(Protocol):
    def get(self, match_id: str) -> StoredMatchMedia | None: ...
    def put(self, media: StoredMatchMedia) -> None: ...
    def ended_matches(self, limit: int) -> list[MatchDetail]: ...


class FirestoreMediaRepository:
    def __init__(self, client: Client): self.client = client
    def _ref(self, match_id: str): return self.client.collection("matches").document(match_id).collection("media").document("current")

    def get(self, match_id: str) -> StoredMatchMedia | None:
        snapshot = self._ref(match_id).get()
        if not snapshot.exists: return None
        try: return StoredMatchMedia(**(snapshot.to_dict() or {}))
        except (TypeError, ValueError): return None

    def put(self, media: StoredMatchMedia) -> None:
        self._ref(media.matchId).set(media.model_dump(mode="json"))

    def ended_matches(self, limit: int) -> list[MatchDetail]:
        matches: list[MatchDetail] = []
        for snapshot in self.client.collection("match_details").limit(max(limit * 5, limit)).stream():
            data = {key: value for key, value in (snapshot.to_dict() or {}).items() if not key.startswith("_")}
            try: detail = MatchDetail(**data)
            except (TypeError, ValueError): continue
            if _is_final(detail): matches.append(detail)
            if len(matches) >= limit: break
        return matches


class YouTubeQuotaError(RuntimeError): pass


class YouTubeOfficialHighlightClient:
    def __init__(self, api_key: str, fifa_channel_id: str, broadcaster_channels_json: str = "{}", timeout: float = 8.0):
        self.api_key, self.fifa_channel_id, self.timeout = api_key.strip(), fifa_channel_id.strip(), timeout
        self.channels = _channel_map(broadcaster_channels_json)
        if self.fifa_channel_id: self.channels[self.fifa_channel_id] = "FIFA YouTube"

    def search(self, match: MatchDetail) -> dict[str, Any] | None:
        if not self.api_key or not self.fifa_channel_id:
            logger.warning("MEDIA_CONFIG_MISSING youtubeApiKey=%s fifaChannelId=%s", bool(self.api_key), bool(self.fifa_channel_id))
            return None
        channel_ids = [self.fifa_channel_id, *(channel for channel in self.channels if channel != self.fifa_channel_id)]
        for channel_id in channel_ids:
            for query in _queries(match):
                payload = self._request(query, channel_id)
                for item in payload.get("items") or []:
                    accepted, reason = validate_official_video(item, match, set(self.channels))
                    logger.info("MEDIA_VALIDATE matchId=%s query=%s accepted=%s reason=%s", match.matchId, query, accepted, reason)
                    if accepted:
                        snippet = item["snippet"]; video_id = item["id"]["videoId"]
                        return {"videoId": video_id, "provider": self.channels.get(snippet.get("channelId"), "Official broadcaster"), "thumbnail": ((snippet.get("thumbnails") or {}).get("high") or (snippet.get("thumbnails") or {}).get("default") or {}).get("url"), "publishedAt": _parse_time(snippet.get("publishedAt")), "query": query}
        return None

    def official_match_url(self, match: MatchDetail) -> str | None:
        if not self.api_key or not self.fifa_channel_id: return None
        home = match.homeTeam.name if match.homeTeam else "Home"
        away = match.awayTeam.name if match.awayTeam else "Away"
        payload = self._request(f"{home} vs {away} FIFA World Cup 2026", self.fifa_channel_id)
        kickoff = _parse_time(match.kickoff)
        for item in payload.get("items") or []:
            snippet = item.get("snippet") or {}; title = str(snippet.get("title") or "").casefold()
            published = _parse_time(snippet.get("publishedAt")); video_id = (item.get("id") or {}).get("videoId")
            if snippet.get("channelId") != self.fifa_channel_id or not video_id or not published or not kickoff or published < kickoff: continue
            if not all(_team_token(name.casefold()) in title for name in (home, away)): continue
            if any(term in title for term in ("press conference", "interview", "train", "training", "goal |", "preview")): continue
            return f"https://www.youtube.com/watch?v={video_id}"
        return None

    def _request(self, query: str, channel_id: str) -> dict[str, Any]:
        try:
            response = httpx.get("https://www.googleapis.com/youtube/v3/search", params={"part": "snippet", "type": "video", "maxResults": 10, "order": "date", "q": query, "channelId": channel_id, "key": self.api_key}, timeout=self.timeout)
            if response.status_code in (403, 429): raise YouTubeQuotaError("YouTube quota or rate limit reached")
            response.raise_for_status(); return response.json()
        except YouTubeQuotaError: raise
        except (httpx.HTTPError, ValueError) as exc: raise RuntimeError("YouTube search failed") from exc


class MatchMediaResolverService:
    def __init__(self, repository: MediaRepository, youtube: YouTubeOfficialHighlightClient, now=lambda: datetime.now(timezone.utc)):
        self.repository, self.youtube, self.now = repository, youtube, now

    def resolve(self, match: MatchDetail, force: bool = False) -> StoredMatchMedia:
        current, now = self.repository.get(match.matchId), self.now()
        if current and not force and not _is_stale(current, match, now): return current.model_copy(update={"source": "cache"})
        kickoff = _parse_time(match.kickoff)
        if not _is_final(match) or kickoff is None or now < kickoff:
            return self._store(_fallback(match.matchId, "pending", now))
        try: candidate = self.youtube.search(match)
        except (YouTubeQuotaError, RuntimeError) as exc:
            logger.warning("MEDIA_YOUTUBE_FAILED matchId=%s reason=%s", match.matchId, exc)
            return current or self._store(_fallback(match.matchId, "pending", now))
        if candidate:
            video_id = candidate["videoId"]
            result = StoredMatchMedia(matchId=match.matchId, highlightStatus="available", highlightProvider=candidate["provider"], highlightUrl=f"https://www.youtube.com/watch?v={video_id}", highlightEmbedUrl=f"https://www.youtube.com/embed/{video_id}", youtubeVideoId=video_id, thumbnailUrl=candidate["thumbnail"], officialHighlightsPageUrl=FIFA_HIGHLIGHTS_URL, source="youtube_api", lastCheckedAt=now, publishedAt=candidate["publishedAt"])
            if current and current.highlightStatus == "available" and current.publishedAt and result.publishedAt and result.publishedAt <= current.publishedAt: return current
            logger.info("MEDIA_RESOLVED matchId=%s source=youtube_api query=%s", match.matchId, candidate["query"])
            return self._store(result)
        try: official_match_url = self.youtube.official_match_url(match)
        except (YouTubeQuotaError, RuntimeError): official_match_url = current.officialMatchUrl if current else None
        ended_at = kickoff + timedelta(hours=2)
        status = "pending" if now - ended_at < timedelta(hours=24) else "not_found"
        return self._store(_fallback(match.matchId, status, now, official_match_url))

    def _store(self, value: StoredMatchMedia) -> StoredMatchMedia:
        self.repository.put(value); return value


def validate_official_video(item: dict[str, Any], match: MatchDetail, allowed_channels: set[str]) -> tuple[bool, str]:
    snippet = item.get("snippet") or {}; title = str(snippet.get("title") or "").casefold()
    if snippet.get("channelId") not in allowed_channels: return False, "channel_not_allowed"
    if not any(word in title for word in ("highlight", "highlights", "resumen", "résumé", "zusammenfassung")): return False, "not_highlights"
    teams = [team.name.casefold() for team in (match.homeTeam, match.awayTeam) if team]
    if teams and not any(_team_token(team) in title for team in teams): return False, "team_missing"
    published, kickoff = _parse_time(snippet.get("publishedAt")), _parse_time(match.kickoff)
    if not published or not kickoff or published < kickoff: return False, "published_before_kickoff"
    if not (item.get("id") or {}).get("videoId"): return False, "video_id_missing"
    return True, "accepted"


def _queries(match: MatchDetail) -> list[str]:
    home, away = match.homeTeam.name if match.homeTeam else "Home", match.awayTeam.name if match.awayTeam else "Away"
    queries = [f"{home} {away} highlights FIFA World Cup 2026", f"{home} vs {away} highlights FIFA World Cup 2026"]
    if match.homeTeam and match.awayTeam and match.homeTeam.score is not None and match.awayTeam.score is not None: queries.append(f"{home} {match.homeTeam.score}-{match.awayTeam.score} {away} highlights FIFA World Cup 2026")
    queries.append(f"{home} v {away} FIFA highlights"); return queries


def _fallback(match_id: str, status: str, now: datetime, official_match_url: str | None = None) -> StoredMatchMedia:
    return StoredMatchMedia(matchId=match_id, highlightStatus=status, officialHighlightsPageUrl=FIFA_HIGHLIGHTS_URL, officialMatchUrl=official_match_url, source="fifa_fallback", lastCheckedAt=now)


def _channel_map(raw: str) -> dict[str, str]:
    try: value = json.loads(raw or "{}")
    except ValueError: return {}
    if isinstance(value, dict) and "youtubeChannels" in value:
        value = value.get("youtubeChannels") or {}
    if isinstance(value, dict): return {str(key): str(name) for key, name in value.items() if re.fullmatch(r"UC[0-9A-Za-z_-]{20,}", str(key))}
    if isinstance(value, list): return {str(item.get("channelId")): str(item.get("name") or "Official broadcaster") for item in value if isinstance(item, dict) and re.fullmatch(r"UC[0-9A-Za-z_-]{20,}", str(item.get("channelId") or ""))}
    return {}


def _team_token(name: str) -> str: return max((part for part in name.split() if len(part) > 2), key=len, default=name)
def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime): return value.astimezone(timezone.utc)
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError: return None
def _is_final(match: MatchDetail) -> bool: return any(word in f"{match.status or ''} {match.statusDescription or ''}".casefold() for word in ("final", "full time", "ft", "aet", "pens"))
def _is_stale(media: StoredMatchMedia, match: MatchDetail, now: datetime) -> bool:
    age = now - media.lastCheckedAt
    if media.highlightStatus == "available": return age >= timedelta(days=1)
    kickoff = _parse_time(match.kickoff)
    if not kickoff: return age >= timedelta(hours=1)
    since_end = now - (kickoff + timedelta(hours=2))
    return age >= (timedelta(minutes=10) if since_end < timedelta(hours=2) else timedelta(hours=1) if since_end < timedelta(hours=24) else timedelta(days=1))
