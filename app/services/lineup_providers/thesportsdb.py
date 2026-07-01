from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import unicodedata
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from firebase_admin import firestore
from google.cloud.firestore_v1 import Client

from app.schemas.matches import LineupPlayer, TeamLineup
from app.services.lineup_providers.base import MatchMeta, ProviderResult


ALIASES = {
    "congo dr": ("dr congo", "democratic republic of congo", "rdc", "cod"),
    "usa": ("united states", "united states of america", "usmnt"),
    "bosnia herz": ("bosnia and herzegovina", "bosnia herzegovina", "bih"),
    "ivory coast": ("cote d ivoire", "civ"),
    "south africa": ("rsa",), "netherlands": ("holland",),
    "switzerland": ("sui",), "algeria": ("dza", "alg"),
    "cape verde": ("cabo verde", "cpv"),
}


class ProviderMappingStore(Protocol):
    def get(self, event_id: str) -> dict | None: ...
    def write(self, event_id: str, mapping: dict) -> None: ...


class FirestoreProviderMappingStore:
    def __init__(self, client: Client):
        self.client = client

    def _ref(self, event_id: str):
        return self.client.collection("matches").document(event_id).collection("providerMappings").document("theSportsDb")

    def get(self, event_id: str) -> dict | None:
        snapshot = self._ref(event_id).get()
        return snapshot.to_dict() if snapshot.exists else None

    def write(self, event_id: str, mapping: dict) -> None:
        ref = self._ref(event_id)
        previous = ref.get()
        ref.set({**mapping, "updatedAt": firestore.SERVER_TIMESTAMP, **({} if previous.exists else {"createdAt": firestore.SERVER_TIMESTAMP})}, merge=True)


@dataclass
class Candidate:
    event_id: str
    score: float
    reversed: bool
    payload: dict


class TheSportsDbProvider:
    def __init__(self, api_key: str, base_url: str, use_v2: bool, mappings: ProviderMappingStore, timeout: float = 8.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.use_v2 = use_v2
        self.mappings = mappings
        self.timeout = timeout

    def fetch(self, meta: MatchMeta) -> ProviderResult:
        attempts: list[dict] = []
        if not self.api_key:
            return ProviderResult(attempts=[{"provider": "theSportsDb", "step": "configuration", "success": False, "reason": "API key missing"}])
        mapping = self.mappings.get(meta.event_id)
        attempts.append({"provider": "theSportsDb", "step": "mapping_cache", "success": bool(mapping), "reason": None if mapping else "no mapping"})
        if mapping and mapping.get("providerEventId"):
            provider_id, reversed_ = str(mapping["providerEventId"]), bool(mapping.get("reversed"))
        else:
            candidate = self._resolve(meta, attempts)
            if candidate is None:
                return ProviderResult(attempts=attempts)
            provider_id, reversed_ = candidate.event_id, candidate.reversed
            self.mappings.write(meta.event_id, {
                "appEventId": meta.event_id, "provider": "theSportsDb", "providerEventId": provider_id,
                "homeTeam": meta.home_team, "awayTeam": meta.away_team,
                "kickoff": meta.kickoff.isoformat() if meta.kickoff else None,
                "confidence": candidate.score, "matchedBy": "search", "reversed": reversed_,
            })
        for step, url in self._lineup_urls(provider_id):
            payload = self._get(url, step, attempts)
            if payload is None:
                continue
            lineups = parse_lineup_payload(payload, meta, reversed_)
            attempts[-1].update(playersExtracted=sum(len(x.starters) + len(x.substitutes) for x in lineups), homeStarters=len(lineups[0].starters) if lineups else 0, awayStarters=len(lineups[1].starters) if len(lineups) > 1 else 0, success=has_players(lineups))
            if has_players(lineups):
                return ProviderResult(lineups=lineups, attempts=attempts)
        return ProviderResult(attempts=attempts)

    def _resolve(self, meta: MatchMeta, attempts: list[dict]) -> Candidate | None:
        date = meta.kickoff.date().isoformat() if meta.kickoff else None
        names = event_search_names(meta.home_team, meta.away_team)
        searches: list[tuple[str, str]] = []
        for name in names[:4]:
            searches.append(("searchevents", self._v1(f"searchevents.php?e={quote(name)}" + (f"&d={date}" if date else ""))))
        if date:
            searches.append(("eventsday", self._v1(f"eventsday.php?d={date}&s=Soccer")))
            for filename in filename_searches(meta, date):
                searches.append(("searchfilename", self._v1(f"searchfilename.php?e={quote(filename)}")))
        if self.use_v2:
            searches.append(("search_event_v2", f"{self.base_url}/api/v2/json/search/event/{quote(names[0])}"))
        best: Candidate | None = None
        for step, url in searches:
            payload = self._get(url, step, attempts)
            candidates = event_candidates(payload) if payload else []
            scored = [score_candidate(item, meta) for item in candidates]
            scored = [item for item in scored if item and item.score >= .75]
            candidate = max(scored, key=lambda item: item.score, default=None)
            attempts[-1].update(candidateCount=len(candidates), bestScore=candidate.score if candidate else None, providerEventId=candidate.event_id if candidate else None, success=bool(candidate))
            if candidate and (best is None or candidate.score > best.score):
                best = candidate
            if best and best.score >= .95:
                break
        return best

    def _get(self, url: str, step: str, attempts: list[dict]) -> Any | None:
        safe_url = url.replace(f"/{self.api_key}/", "/***/")
        attempt = {"provider": "theSportsDb", "step": step, "url": safe_url, "success": False}
        attempts.append(attempt)
        try:
            response = httpx.get(url, timeout=self.timeout, follow_redirects=True)
            attempt["httpStatus"] = response.status_code
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            attempt["reason"] = str(exc).replace(self.api_key, "***")[:200]
            return None

    def _v1(self, path: str) -> str:
        return f"{self.base_url}/api/v1/json/{self.api_key}/{path}"

    def _lineup_urls(self, event_id: str):
        yield "lookuplineup_v1", self._v1(f"lookuplineup.php?id={quote(event_id)}")
        if self.use_v2:
            yield "event_lineup_v2", f"{self.base_url}/api/v2/json/lookup/event_lineup/{quote(event_id)}"


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", (value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def name_variants(value: str) -> set[str]:
    normalized = normalize_name(value)
    result = {normalized}
    for canonical, aliases in ALIASES.items():
        group = {normalize_name(canonical), *(normalize_name(item) for item in aliases)}
        if normalized in group:
            result.update(group)
    return result


def names_match(left: str | None, right: str) -> bool:
    return bool(name_variants(left or "") & name_variants(right))


def event_search_names(home: str, away: str) -> list[str]:
    pairs = [(home, away), (away, home)]
    if "congo" in normalize_name(away): pairs += [(home, "DR Congo"), (home, "Democratic Republic of Congo")]
    return list(dict.fromkeys(f"{a}_vs_{b}" for a, b in pairs))


def filename_searches(meta: MatchMeta, date: str) -> list[str]:
    pair = f"{meta.home_team}_vs_{meta.away_team}".replace(" ", "_")
    return [f"FIFA_World_Cup_{date}_{pair}", f"FIFA_World_Cup_2026_{date}_{pair}", f"Soccer_{date}_{pair}", f"World_Cup_{date}_{pair}"]


def event_candidates(payload: Any) -> list[dict]:
    if not isinstance(payload, dict): return []
    for key in ("event", "events"):
        if isinstance(payload.get(key), list): return [x for x in payload[key] if isinstance(x, dict)]
    return []


def score_candidate(item: dict, meta: MatchMeta) -> Candidate | None:
    home = item.get("strHomeTeam") or item.get("homeTeam") or item.get("strEvent", "").split(" vs ")[0]
    away = item.get("strAwayTeam") or item.get("awayTeam") or (item.get("strEvent", "").split(" vs ")[1] if " vs " in item.get("strEvent", "") else "")
    direct = names_match(home, meta.home_team) and names_match(away, meta.away_team)
    reversed_ = names_match(home, meta.away_team) and names_match(away, meta.home_team)
    if not direct and not reversed_: return None
    score = .8
    candidate_date = str(item.get("dateEvent") or item.get("date") or "")[:10]
    if meta.kickoff and candidate_date == meta.kickoff.date().isoformat(): score += .1
    candidate_time = str(item.get("strTime") or item.get("strTimestamp") or "")
    if meta.kickoff and candidate_time:
        try:
            if "T" in candidate_time:
                parsed_time = datetime.fromisoformat(candidate_time.replace("Z", "+00:00"))
            else:
                parsed_time = datetime.fromisoformat(f"{candidate_date}T{candidate_time.replace('Z', '+00:00')}")
            if parsed_time.tzinfo is None: parsed_time = parsed_time.replace(tzinfo=timezone.utc)
            if abs((parsed_time - meta.kickoff).total_seconds()) <= 7200: score += .05
        except ValueError:
            pass
    league = f"{item.get('strLeague', '')} {item.get('strEvent', '')}".casefold()
    if "world cup" in league or "fifa" in league: score += .05
    event_id = item.get("idEvent") or item.get("id")
    return Candidate(str(event_id), min(score, 1), reversed_, item) if event_id else None


def parse_lineup_payload(payload: Any, meta: MatchMeta, reversed_: bool = False) -> list[TeamLineup]:
    items = _player_items(payload)
    home = TeamLineup(teamId=meta.home_team_id, teamName=meta.home_team)
    away = TeamLineup(teamId=meta.away_team_id, teamName=meta.away_team)
    seen: set[tuple[str, str]] = set()
    for item in items:
        name = _first(item, "strPlayer", "strPlayerName", "name", "player", "strName")
        if not name: continue
        team_name = _first(item, "strTeam", "team", "teamName") or ""
        side = item.get("_providerSide")
        home_marker = normalize_name(str(item.get("strHome") or ""))
        if home_marker in {"yes", "true", "1", "home"}: side = "home"
        elif home_marker in {"no", "false", "0", "away"}: side = "away"
        provider_home = names_match(team_name, meta.away_team if reversed_ else meta.home_team) or side == ("away" if reversed_ else "home")
        provider_away = names_match(team_name, meta.home_team if reversed_ else meta.away_team) or side == ("home" if reversed_ else "away")
        target = home if provider_home else away if provider_away else None
        if target is None: continue
        key = (normalize_name(name), target.teamName or "")
        if key in seen: continue
        seen.add(key)
        role = str(_first(item, "strLineup", "strSubstitute", "type", "role") or "")
        folded = role.casefold()
        bench = "substitute" in folded or "bench" in folded or folded in {"yes", "true", "1"}
        starter = "starting" in folded or "starter" in folded or "lineup" in folded or folded in {"no", "false", "0"}
        player = LineupPlayer(name=str(name), id=str(item.get("idPlayer")) if item.get("idPlayer") else None,
                              position=_first(item, "strPosition", "position", "pos"), jersey=str(_first(item, "intSquadNumber", "intNumber", "number", "shirtNumber") or "") or None,
                              starter=starter and not bench, substitute=bench, role="Bench" if bench else "Starter")
        if bench: target.substitutes.append(player)
        elif starter or len(target.starters) < 11: target.starters.append(player)
        else: target.substitutes.append(player)
        formation = _first(item, "strFormation", "formation")
        if formation: target.formation = str(formation)
    for target in (home, away):
        if len(target.starters) > 11:
            target.substitutes = target.starters[11:] + target.substitutes
            target.starters = target.starters[:11]
    return [home, away] if has_players([home, away]) else []


def _player_items(value: Any, side: str | None = None) -> list[dict]:
    found: list[dict] = []
    if isinstance(value, list):
        for child in value: found.extend(_player_items(child, side))
    elif isinstance(value, dict):
        if any(key in value for key in ("strPlayer", "strPlayerName", "player", "strName")):
            found.append({**value, **({"_providerSide": side} if side else {})})
        else:
            for key in ("lineup", "lineups", "event_lineup", "eventlineup", "players", "events", "home", "away"):
                if key in value: found.extend(_player_items(value[key], key if key in {"home", "away"} else side))
    return found


def _first(item: dict, *keys: str):
    return next((item[key] for key in keys if item.get(key) not in (None, "")), None)


def has_players(lineups: list[TeamLineup]) -> bool:
    return any(team.starters or team.substitutes for team in lineups)
