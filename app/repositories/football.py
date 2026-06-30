import unicodedata
from typing import Protocol

from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.base_query import FieldFilter

from app.schemas.football import PlayerResult, TeamResult


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
    def search_teams(self, query: str) -> list[TeamResult]: ...

    def search_players(self, query: str) -> list[PlayerResult]: ...


class FirestoreFootballRepository:
    def __init__(self, client: Client):
        self.client = client

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

        player_data = [snapshot.to_dict() for snapshot in snapshots]
        team_ids = {
            int(team_id)
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
            int(snapshot.id): snapshot.to_dict().get("name", "")
            for snapshot in team_snapshots
            if snapshot.exists
        }
        return [
            PlayerResult(
                id=str(player["id"]),
                name=player["name"],
                team=", ".join(
                    team_names[team_id]
                    for team_id in player.get("team_ids", [])
                    if team_id in team_names
                ),
                imageUrl=player.get("photo"),
            )
            for player in player_data
        ]
