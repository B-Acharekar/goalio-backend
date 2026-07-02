from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from typing import Protocol

from google.cloud.firestore_v1 import Client

from app.schemas.matches import MatchDetail
from app.schemas.media import MatchWatchResponse, WatchProvider, WatchProviderConfig


logger = logging.getLogger(__name__)
DISCLAIMER = "Streaming availability depends on your region and broadcaster rights."
NO_REGION_MESSAGE = "Live broadcaster information is not available for your region yet."
FIFA_FALLBACK = WatchProvider(name="FIFA Match Centre", type="official_page", url="https://www.fifa.com/en/match-centre", note="Official FIFA match information.")
BLOCKED_TERMS = {"free stream", "pirate", "vipbox", "rojadirecta", "totalsportek", "crackstreams"}


class WatchProviderRepository(Protocol):
    def get_config(self, competition: str, season: str) -> WatchProviderConfig | None: ...
    def cache_match(self, match_id: str, config: WatchProviderConfig | None, source: str) -> None: ...


class FirestoreWatchProviderRepository:
    def __init__(self, client: Client, environment_json: str = "{}"): self.client, self.environment_json = client, environment_json
    def _key(self, competition: str, season: str) -> str: return re.sub(r"[^a-z0-9]+", "-", f"{competition}-{season}".casefold()).strip("-")

    def get_config(self, competition: str, season: str) -> WatchProviderConfig | None:
        snapshot = self.client.collection("watch_providers").document(self._key(competition, season)).get()
        if snapshot.exists:
            try: return WatchProviderConfig(**(snapshot.to_dict() or {}))
            except (TypeError, ValueError): pass
        return environment_watch_config(self.environment_json, competition, season)

    def cache_match(self, match_id: str, config: WatchProviderConfig | None, source: str) -> None:
        ref = self.client.collection("matches").document(match_id).collection("watch").document("current")
        ref.set({"matchId": match_id, "competition": config.competition if config else None, "season": config.season if config else None, "providersByRegion": config.model_dump(mode="json")["regions"] if config else {}, "fallback": (config.fallback if config else FIFA_FALLBACK).model_dump(mode="json"), "lastCheckedAt": datetime.now(timezone.utc), "source": source})


class WatchProviderResolverService:
    def __init__(self, repository: WatchProviderRepository): self.repository = repository

    def resolve(self, match: MatchDetail, country: str | None) -> MatchWatchResponse:
        normalized = normalize_country(country)
        competition, season = _competition(match), _season(match)
        config = self.repository.get_config(competition, season)
        providers = list((config.regions.get(normalized) or config.regions.get("GLOBAL") or []) if config else [])
        providers = [provider for provider in providers if is_legal_provider(provider)]
        fallback = config.fallback if config else FIFA_FALLBACK
        self.repository.cache_match(match.matchId, config, "config" if config else "fifa_fallback")
        logger.info("WATCH_RESOLVED matchId=%s country=%s providers=%s fallback=%s", match.matchId, normalized, len(providers), not providers)
        return MatchWatchResponse(matchId=match.matchId, country=normalized, status="available" if providers else "not_available", providers=providers, fallback=fallback, message=None if providers else NO_REGION_MESSAGE, disclaimer=DISCLAIMER)


def normalize_country(country: str | None) -> str:
    value = (country or "").strip().upper()
    return value if re.fullmatch(r"[A-Z]{2}", value) else "GLOBAL"


def validate_provider(provider: WatchProvider) -> None:
    if not is_legal_provider(provider): raise ValueError("Provider URL or name is not an approved legal destination")


def is_legal_provider(provider: WatchProvider) -> bool:
    text = f"{provider.name} {provider.url}".casefold()
    return str(provider.url).startswith("https://") and not any(term in text for term in BLOCKED_TERMS)


def environment_watch_config(raw: str, competition: str, season: str) -> WatchProviderConfig | None:
    try: root = json.loads(raw or "{}")
    except ValueError: return None
    mappings = root.get("watchProviders", root) if isinstance(root, dict) else {}
    if not isinstance(mappings, dict): return None
    key = re.sub(r"[^a-z0-9]+", "-", f"{competition}-{season}".casefold()).strip("-")
    value = mappings.get(f"{competition}:{season}") or mappings.get(key)
    if value is None and any(re.fullmatch(r"[A-Z]{2}|GLOBAL", str(item)) for item in mappings):
        value = {"regions": mappings}
    if value is None and "regions" in mappings: value = mappings
    if not isinstance(value, dict): return None
    payload = {"competition": competition, "season": season, "regions": value.get("regions") or {}, "fallback": value.get("fallback") or FIFA_FALLBACK.model_dump(mode="json"), "updatedAt": value.get("updatedAt") or datetime.now(timezone.utc)}
    try: return WatchProviderConfig(**payload)
    except (TypeError, ValueError): return None


def _competition(match: MatchDetail) -> str:
    return "FIFA World Cup" if match.league == "fifa.world" else match.league


def _season(match: MatchDetail) -> str:
    try: return str(datetime.fromisoformat((match.kickoff or "").replace("Z", "+00:00")).year)
    except ValueError: return "2026"
