import unicodedata
from time import monotonic
from typing import NoReturn, Protocol

from fastapi import HTTPException, status
from google.api_core.exceptions import GoogleAPICallError
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.base_query import FieldFilter

from app.schemas.football import PlayerPage, PlayerResult, TeamPage, TeamResult


CACHE_TTL_SECONDS = 15 * 60


class _CatalogCache:
    def __init__(self):
        self.values: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        cached = self.values.get(key)
        if cached is None:
            return None
        expires_at, value = cached
        if expires_at <= monotonic():
            self.values.pop(key, None)
            return None
        return value

    def set(self, key: str, value):
        self.values[key] = (monotonic() + CACHE_TTL_SECONDS, value)


_cache = _CatalogCache()


def _raise_firestore_unavailable(exc: GoogleAPICallError) -> NoReturn:
    detail = (
        "Cloud Firestore quota is exhausted or temporarily unavailable. "
        "Wait for quota reset, reduce catalog/search calls, or upgrade the Firebase plan."
    )
    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail) from exc


def normalize_search(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join("".join(character if character.isalnum() else " " for character in ascii_text).split())


def search_terms(value: str) -> list[str]:
    normalized = normalize_search(value)
    sources = {normalized, *normalized.split()}
    return sorted(
        {source[:length] for source in sources for length in range(1, len(source) + 1)}
    )


class FootballRepository(Protocol):
    def list_teams(self, limit: int, cursor: str | None) -> TeamPage: ...

    def list_players(self, limit: int, cursor: str | None) -> PlayerPage: ...

    def search_teams(self, query: str) -> list[TeamResult]: ...

    def search_players(self, query: str) -> list[PlayerResult]: ...


class FirestoreFootballRepository:
    def __init__(self, client: Client):
        self.client = client

    def _active_page(self, collection_name: str, limit: int, cursor: str | None):
        collection = self.client.collection(collection_name)
        query = collection.where(filter=FieldFilter("active", "==", True)).order_by("__name__")
        if cursor:
            cursor_snapshot = collection.document(cursor).get()
            if cursor_snapshot.exists:
                query = query.start_after(cursor_snapshot)
        try:
            snapshots = list(query.limit(limit + 1).stream())
        except GoogleAPICallError as exc:
            _raise_firestore_unavailable(exc)
        has_more = len(snapshots) > limit
        page = snapshots[:limit]
        return page, page[-1].id if has_more and page else None

    def list_teams(self, limit: int, cursor: str | None) -> TeamPage:
        cache_key = f"teams:list:{limit}:{cursor or ''}"
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
        snapshots, next_cursor = self._active_page("teams", limit, cursor)
        page = TeamPage(
            items=[
                TeamResult(
                    id=str(data["id"]),
                    name=data["name"],
                    shortName=data.get("code") or data["name"][:3].upper(),
                    competitionIds=[int(item) for item in data.get("competition_ids", [])],
                    imageUrl=data.get("logo"),
                )
                for snapshot in snapshots
                for data in [snapshot.to_dict()]
            ],
            nextCursor=next_cursor,
        )
        _cache.set(cache_key, page)
        return page

    def list_players(self, limit: int, cursor: str | None) -> PlayerPage:
        cache_key = f"players:list:{limit}:{cursor or ''}"
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
        snapshots, next_cursor = self._active_page("players", limit, cursor)
        page = PlayerPage(
            items=self._player_results([snapshot.to_dict() for snapshot in snapshots]),
            nextCursor=next_cursor,
        )
        _cache.set(cache_key, page)
        return page

    def search_teams(self, query: str) -> list[TeamResult]:
        normalized = normalize_search(query)
        cache_key = f"teams:search:{normalized}"
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
        collection = self.client.collection("teams")
        try:
            if normalized:
                snapshots = collection.where(
                    filter=FieldFilter("search_terms", "array_contains", normalized)
                ).limit(20).stream()
            else:
                snapshots = collection.where(
                    filter=FieldFilter("active", "==", True)
                ).limit(20).stream()
            snapshot_list = list(snapshots)
        except GoogleAPICallError as exc:
            _raise_firestore_unavailable(exc)
        results = [
            TeamResult(
                id=str(data["id"]),
                name=data["name"],
                shortName=data.get("code") or data["name"][:3].upper(),
                competitionIds=[int(item) for item in data.get("competition_ids", [])],
                imageUrl=data.get("logo"),
            )
            for snapshot in snapshot_list
            for data in [snapshot.to_dict()]
        ]
        _cache.set(cache_key, results)
        return results

    def search_players(self, query: str) -> list[PlayerResult]:
        normalized = normalize_search(query)
        cache_key = f"players:search:{normalized}"
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
        collection = self.client.collection("players")
        try:
            if normalized:
                snapshots = list(
                    collection.where(
                        filter=FieldFilter("search_terms", "array_contains", normalized)
                    ).limit(20).stream()
                )
            else:
                snapshots = list(
                    collection.where(filter=FieldFilter("active", "==", True)).limit(20).stream()
                )
        except GoogleAPICallError as exc:
            _raise_firestore_unavailable(exc)

        results = self._player_results([snapshot.to_dict() for snapshot in snapshots])
        _cache.set(cache_key, results)
        return results

    def _player_results(self, player_data: list[dict]) -> list[PlayerResult]:
        team_ids = {
            str(team_id)
            for player in player_data
            for team_id in player.get("team_ids", [])
        }
        try:
            team_snapshots = (
                list(
                    self.client.get_all(
                        [
                            self.client.collection("teams").document(str(team_id))
                            for team_id in team_ids
                        ]
                    )
                )
                if team_ids
                else []
            )
        except GoogleAPICallError as exc:
            _raise_firestore_unavailable(exc)
        team_names = {
            str(snapshot.id): snapshot.to_dict().get("name", "")
            for snapshot in team_snapshots
            if snapshot.exists
        }
        team_competitions = {
            str(snapshot.id): [
                int(item) for item in snapshot.to_dict().get("competition_ids", [])
            ]
            for snapshot in team_snapshots
            if snapshot.exists
        }
        return [
            PlayerResult(
                id=str(player["id"]),
                name=player["name"],
                team=", ".join(
                    team_names[str(team_id)]
                    for team_id in player.get("team_ids", [])
                    if str(team_id) in team_names
                ),
                competitionIds=sorted(
                    {
                        competition_id
                        for team_id in player.get("team_ids", [])
                        for competition_id in team_competitions.get(str(team_id), [])
                    }
                ),
                imageUrl=player.get("photo"),
            )
            for player in player_data
        ]
