import unicodedata
from typing import Protocol

from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.base_query import FieldFilter

from app.schemas.football import PlayerPage, PlayerResult, TeamPage, TeamResult


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
        snapshots = list(query.limit(limit + 1).stream())
        has_more = len(snapshots) > limit
        page = snapshots[:limit]
        return page, page[-1].id if has_more and page else None

    def list_teams(self, limit: int, cursor: str | None) -> TeamPage:
        snapshots, next_cursor = self._active_page("teams", limit, cursor)
        return TeamPage(
            items=[
                TeamResult(
                    id=str(data["id"]),
                    name=data["name"],
                    shortName=data.get("code") or data["name"][:3].upper(),
                    imageUrl=data.get("logo"),
                )
                for snapshot in snapshots
                for data in [snapshot.to_dict()]
            ],
            nextCursor=next_cursor,
        )

    def list_players(self, limit: int, cursor: str | None) -> PlayerPage:
        snapshots, next_cursor = self._active_page("players", limit, cursor)
        return PlayerPage(
            items=self._player_results([snapshot.to_dict() for snapshot in snapshots]),
            nextCursor=next_cursor,
        )

    def search_teams(self, query: str) -> list[TeamResult]:
        normalized = normalize_search(query)
        collection = self.client.collection("teams")
        if normalized:
            snapshots = collection.where(
                filter=FieldFilter("search_terms", "array_contains", normalized)
            ).limit(20).stream()
        else:
            snapshots = collection.where(
                filter=FieldFilter("active", "==", True)
            ).limit(20).stream()
        return [
            TeamResult(
                id=str(data["id"]),
                name=data["name"],
                shortName=data.get("code") or data["name"][:3].upper(),
                imageUrl=data.get("logo"),
            )
            for snapshot in snapshots
            for data in [snapshot.to_dict()]
        ]

    def search_players(self, query: str) -> list[PlayerResult]:
        normalized = normalize_search(query)
        collection = self.client.collection("players")
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

        return self._player_results([snapshot.to_dict() for snapshot in snapshots])

    def _player_results(self, player_data: list[dict]) -> list[PlayerResult]:
        team_ids = {
            str(team_id)
            for player in player_data
            for team_id in player.get("team_ids", [])
        }
        team_snapshots = (
            self.client.get_all(
                [self.client.collection("teams").document(str(team_id)) for team_id in team_ids]
            )
            if team_ids
            else []
        )
        team_names = {
            str(snapshot.id): snapshot.to_dict().get("name", "")
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
                imageUrl=player.get("photo"),
            )
            for player in player_data
        ]
