from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

import httpx
from firebase_admin import firestore
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.base_query import FieldFilter

from app.repositories.football import search_terms


API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"


@dataclass(frozen=True)
class Competition:
    id: int
    name: str


COMPETITIONS = (
    Competition(39, "Premier League"),
    Competition(140, "LaLiga"),
    Competition(135, "Serie A"),
    Competition(78, "Bundesliga"),
    Competition(61, "Ligue 1"),
    Competition(1, "World Cup"),
)
COMPETITIONS_BY_ID = {competition.id: competition for competition in COMPETITIONS}


@dataclass(frozen=True)
class SyncState:
    team_ids: tuple[int, ...]
    next_team_index: int
    status: str


@dataclass(frozen=True)
class SyncResult:
    competition_id: int
    season: int
    requests_used: int
    teams_processed: int
    completed: bool


class MasterDataStore(Protocol):
    def load_state(self, competition_id: int, season: int) -> SyncState | None: ...

    def initialize_competition(
        self, competition: Competition, season: int, teams: list[dict[str, Any]]
    ) -> SyncState: ...

    def replace_team_squad(
        self,
        competition: Competition,
        season: int,
        team_id: int,
        players: list[dict[str, Any]],
    ) -> None: ...

    def advance(self, competition_id: int, season: int, next_team_index: int) -> None: ...

    def complete(self, competition_id: int, season: int) -> None: ...


class ApiFootballError(RuntimeError):
    pass


class ApiFootballClient:
    def __init__(self, api_key: str, timeout_seconds: float = 20.0):
        if not api_key.strip():
            raise ValueError("API_FOOTBALL_KEY is required")
        self._client = httpx.Client(
            base_url=API_FOOTBALL_BASE_URL,
            headers={"x-apisports-key": api_key.strip()},
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, int]) -> list[dict[str, Any]]:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors")
        if errors:
            raise ApiFootballError(f"API-Football rejected {path}: {errors}")
        items = payload.get("response")
        if not isinstance(items, list):
            raise ApiFootballError(f"API-Football returned an invalid response for {path}")
        return items

    def teams(self, competition_id: int, season: int) -> list[dict[str, Any]]:
        teams = self._get("/teams", {"league": competition_id, "season": season})
        if not teams:
            raise ApiFootballError(
                f"No teams are published for competition {competition_id}, season {season}"
            )
        return teams

    def squad(self, team_id: int) -> list[dict[str, Any]]:
        response = self._get("/players/squads", {"team": team_id})
        if not response:
            raise ApiFootballError(f"No squad is published for team {team_id}")
        players = response[0].get("players", [])
        if not isinstance(players, list) or not players:
            raise ApiFootballError(f"Invalid squad response for team {team_id}")
        return players

    def season_start(self, competition_id: int, season: int) -> date | None:
        response = self._get("/leagues", {"id": competition_id, "season": season})
        for league in response:
            for item in league.get("seasons", []):
                if item.get("year") == season and item.get("start"):
                    return date.fromisoformat(item["start"])
        return None


def is_sync_due(season_start: date | None, today: date) -> bool:
    if season_start is None:
        return False
    return season_start - timedelta(days=7) <= today <= season_start + timedelta(days=14)


class FirestoreMasterDataStore:
    def __init__(self, client: Client):
        self.client = client

    @staticmethod
    def _state_id(competition_id: int, season: int) -> str:
        return f"{competition_id}_{season}"

    def load_state(self, competition_id: int, season: int) -> SyncState | None:
        snapshot = self.client.collection("master_data_sync").document(
            self._state_id(competition_id, season)
        ).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        return SyncState(
            team_ids=tuple(int(team_id) for team_id in data.get("teamIds", [])),
            next_team_index=int(data.get("nextTeamIndex", 0)),
            status=data.get("status", "in_progress"),
        )

    def _remove_mappings(self, mappings: list[Any]) -> None:
        removed_teams_by_player: dict[int, set[int]] = {}
        for mapping in mappings:
            data = mapping.to_dict()
            if data.get("playerId") is not None and data.get("teamId") is not None:
                removed_teams_by_player.setdefault(int(data["playerId"]), set()).add(
                    int(data["teamId"])
                )

        player_refs = {
            player_id: self.client.collection("players").document(str(player_id))
            for player_id in removed_teams_by_player
        }
        player_snapshots = (
            {
                int(snapshot.id): snapshot
                for snapshot in self.client.get_all(list(player_refs.values()))
                if snapshot.exists
            }
            if player_refs
            else {}
        )
        batch = self.client.batch()
        operation_count = 0
        for mapping in mappings:
            batch.delete(mapping.reference)
            operation_count += 1
            if operation_count >= 400:
                batch.commit()
                batch = self.client.batch()
                operation_count = 0
        for player_id, removed_team_ids in removed_teams_by_player.items():
            snapshot = player_snapshots.get(player_id)
            if snapshot is None:
                continue
            data = snapshot.to_dict()
            remaining_team_ids = [
                int(team_id)
                for team_id in data.get("team_ids", [])
                if int(team_id) not in removed_team_ids
            ]
            update = {
                "team_ids": remaining_team_ids,
                "active": bool(remaining_team_ids),
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }
            if not remaining_team_ids:
                update["search_terms"] = []
            batch.set(player_refs[player_id], update, merge=True)
            operation_count += 1
            if operation_count >= 400:
                batch.commit()
                batch = self.client.batch()
                operation_count = 0
        if operation_count:
            batch.commit()

    def _deactivate_old_teams(self, competition_id: int, current_team_ids: set[int]) -> None:
        old_teams = self.client.collection("teams").where(
            filter=FieldFilter("competition_ids", "array_contains", competition_id)
        ).stream()
        batch = self.client.batch()
        operation_count = 0
        for snapshot in old_teams:
            team_id = int(snapshot.id)
            if team_id in current_team_ids:
                continue
            data = snapshot.to_dict()
            remaining_competitions = [
                int(item)
                for item in data.get("competition_ids", [])
                if int(item) != competition_id
            ]
            update = {
                "competition_ids": remaining_competitions,
                "active": bool(remaining_competitions),
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }
            if not remaining_competitions:
                update["search_terms"] = []
            batch.set(snapshot.reference, update, merge=True)
            operation_count += 1
            if operation_count >= 400:
                batch.commit()
                batch = self.client.batch()
                operation_count = 0
        if operation_count:
            batch.commit()

    def initialize_competition(
        self, competition: Competition, season: int, teams: list[dict[str, Any]]
    ) -> SyncState:
        team_ids = [int(item["team"]["id"]) for item in teams]
        batch = self.client.batch()
        for item in teams:
            team = item.get("team", {})
            venue = item.get("venue") or {}
            team_id = int(team["id"])
            batch.set(
                self.client.collection("teams").document(str(team_id)),
                {
                    "id": team_id,
                    "name": team.get("name"),
                    "code": team.get("code"),
                    "country": team.get("country"),
                    "founded": team.get("founded"),
                    "national": bool(team.get("national")),
                    "logo": team.get("logo"),
                    "search_terms": search_terms(team.get("name") or ""),
                    "venue": venue,
                    "competition_ids": firestore.ArrayUnion([competition.id]),
                    "seasons": firestore.ArrayUnion([season]),
                    "updated_season": season,
                    "active": True,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
        if team_ids:
            batch.commit()

        state = SyncState(tuple(team_ids), 0, "in_progress")
        self.client.collection("master_data_sync").document(
            self._state_id(competition.id, season)
        ).set(
            {
                "competitionId": competition.id,
                "competitionName": competition.name,
                "season": season,
                "teamIds": team_ids,
                "nextTeamIndex": 0,
                "status": "in_progress",
                "startedAt": firestore.SERVER_TIMESTAMP,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }
        )
        return state

    def replace_team_squad(
        self,
        competition: Competition,
        season: int,
        team_id: int,
        players: list[dict[str, Any]],
    ) -> None:
        new_player_ids = {int(player["id"]) for player in players}
        existing_mappings = list(
            self.client.collection("team_players")
            .where(filter=FieldFilter("teamId", "==", team_id))
            .stream()
        )
        obsolete_mappings = [
            mapping
            for mapping in existing_mappings
            if int(mapping.to_dict().get("playerId", -1)) not in new_player_ids
        ]
        self._remove_mappings(obsolete_mappings)

        batch = self.client.batch()
        for player in players:
            player_id = int(player["id"])
            position = player.get("position")
            batch.set(
                self.client.collection("players").document(str(player_id)),
                {
                    "id": player_id,
                    "name": player.get("name"),
                    "firstname": player.get("firstname"),
                    "lastname": player.get("lastname"),
                    "age": player.get("age"),
                    "nationality": player.get("nationality"),
                    "photo": player.get("photo"),
                    "search_terms": search_terms(player.get("name") or ""),
                    "positions": [position] if position else [],
                    "team_ids": firestore.ArrayUnion([team_id]),
                    "updated_season": season,
                    "active": True,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            batch.set(
                self.client.collection("team_players").document(f"{team_id}_{player_id}"),
                {
                    "teamId": team_id,
                    "playerId": player_id,
                    "competitionId": competition.id,
                    "season": season,
                    "active": True,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                },
            )
        if players:
            batch.commit()

    def advance(self, competition_id: int, season: int, next_team_index: int) -> None:
        self.client.collection("master_data_sync").document(
            self._state_id(competition_id, season)
        ).update(
            {
                "nextTeamIndex": next_team_index,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }
        )

    def complete(self, competition_id: int, season: int) -> None:
        state_ref = self.client.collection("master_data_sync").document(
            self._state_id(competition_id, season)
        )
        state = state_ref.get().to_dict()
        current_team_ids = {int(team_id) for team_id in state.get("teamIds", [])}
        competition_mappings = self.client.collection("team_players").where(
            filter=FieldFilter("competitionId", "==", competition_id)
        ).stream()
        obsolete_mappings = [
            mapping
            for mapping in competition_mappings
            if int(mapping.to_dict().get("teamId", -1)) not in current_team_ids
        ]
        self._remove_mappings(obsolete_mappings)
        self._deactivate_old_teams(competition_id, current_team_ids)
        state_ref.update(
            {
                "status": "completed",
                "completedAt": firestore.SERVER_TIMESTAMP,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }
        )


class MasterDataSyncService:
    def __init__(self, api: ApiFootballClient, store: MasterDataStore):
        self.api = api
        self.store = store

    def sync_competition(
        self, competition: Competition, season: int, max_requests: int
    ) -> SyncResult:
        if max_requests < 0:
            raise ValueError("max_requests cannot be negative")
        requests_used = 0
        teams_processed = 0
        state = self.store.load_state(competition.id, season)
        if state and state.status == "completed":
            return SyncResult(competition.id, season, 0, 0, True)

        if state is None:
            if max_requests == 0:
                return SyncResult(competition.id, season, 0, 0, False)
            teams = self.api.teams(competition.id, season)
            requests_used += 1
            state = self.store.initialize_competition(competition, season, teams)

        next_index = state.next_team_index
        while next_index < len(state.team_ids) and requests_used < max_requests:
            team_id = state.team_ids[next_index]
            players = self.api.squad(team_id)
            requests_used += 1
            self.store.replace_team_squad(competition, season, team_id, players)
            next_index += 1
            teams_processed += 1
            self.store.advance(competition.id, season, next_index)

        completed = next_index >= len(state.team_ids)
        if completed:
            self.store.complete(competition.id, season)
        return SyncResult(
            competition.id,
            season,
            requests_used,
            teams_processed,
            completed,
        )
