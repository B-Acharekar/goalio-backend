from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import time
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
    espn_code: str


COMPETITIONS = (
    Competition(39, "Premier League", "eng.1"),
    Competition(140, "LaLiga", "esp.1"),
    Competition(135, "Serie A", "ita.1"),
    Competition(78, "Bundesliga", "ger.1"),
    Competition(61, "Ligue 1", "fra.1"),
    Competition(1, "World Cup", "fifa.world"),
)
COMPETITIONS_BY_ID = {competition.id: competition for competition in COMPETITIONS}


@dataclass(frozen=True)
class SyncState:
    team_ids: tuple[str, ...]
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
        team_id: str,
        players: list[dict[str, Any]],
    ) -> None: ...

    def advance(self, competition_id: int, season: int, next_team_index: int) -> None: ...

    def record_failure(
        self, competition_id: int, season: int, team_id: str, error: str
    ) -> None: ...

    def clear_failure(self, competition_id: int, season: int, team_id: str) -> None: ...

    def complete(self, competition_id: int, season: int) -> bool: ...


class ApiFootballError(RuntimeError):
    pass


class ApiFootballClient:
    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 20.0,
        request_interval_seconds: float = 6.2,
        max_requests: int = 95,
    ):
        if not api_key.strip():
            raise ValueError("API_FOOTBALL_KEY is required")
        self._client = httpx.Client(
            base_url=API_FOOTBALL_BASE_URL,
            headers={"x-apisports-key": api_key.strip()},
            timeout=timeout_seconds,
        )
        self._request_interval_seconds = max(0.0, request_interval_seconds)
        self._last_request_at = 0.0
        self._max_requests = max_requests
        self._requests_used = 0

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, int | str]) -> list[dict[str, Any]]:
        response = None
        for attempt in range(2):
            if self._requests_used >= self._max_requests:
                raise ApiFootballError(
                    f"API-Football safety limit of {self._max_requests} requests reached"
                )
            elapsed = time.monotonic() - self._last_request_at
            wait_seconds = self._request_interval_seconds - elapsed
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            response = self._client.get(path, params=params)
            self._requests_used += 1
            self._last_request_at = time.monotonic()
            if response.status_code != 429 or attempt == 1:
                break
            retry_after = float(response.headers.get("Retry-After", "60"))
            time.sleep(max(60.0, retry_after))
        assert response is not None
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

    def squad(self, team_id: str) -> list[dict[str, Any]]:
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


class EspnFootballError(RuntimeError):
    pass


class EspnFootballClient:
    def __init__(self, timeout_seconds: float = 20.0, request_interval_seconds: float = 0.5):
        self._client = httpx.Client(
            base_url="https://site.api.espn.com",
            timeout=timeout_seconds,
            headers={"User-Agent": "GoalioMasterData/1.0"},
        )
        self._request_interval_seconds = max(0.0, request_interval_seconds)
        self._last_request_at = 0.0

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str) -> dict[str, Any]:
        elapsed = time.monotonic() - self._last_request_at
        wait_seconds = self._request_interval_seconds - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        response = self._client.get(path)
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise EspnFootballError(f"ESPN returned an invalid response for {path}")
        return payload

    def teams(self, competition_id: int, season: int) -> list[dict[str, Any]]:
        competition = COMPETITIONS_BY_ID[competition_id]
        payload = self._get(
            f"/apis/site/v2/sports/soccer/{competition.espn_code}/teams"
        )
        try:
            league = payload["sports"][0]["leagues"][0]
            published_season = int(league["season"]["year"])
            raw_teams = league["teams"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise EspnFootballError(
                f"ESPN teams are unavailable for {competition.name}"
            ) from exc
        if published_season != season:
            raise EspnFootballError(
                f"ESPN {competition.name} currently publishes season {published_season}, not {season}"
            )

        teams: list[dict[str, Any]] = []
        for item in raw_teams:
            team = item.get("team") or {}
            source_id = str(team.get("id", "")).strip()
            if not source_id:
                continue
            logos = team.get("logos") or []
            teams.append(
                {
                    "team": {
                        "id": f"espn_{competition.espn_code}_{source_id}",
                        "source": "espn",
                        "sourceId": source_id,
                        "name": team.get("displayName") or team.get("name"),
                        "code": team.get("abbreviation"),
                        "country": None,
                        "founded": None,
                        "national": competition.espn_code == "fifa.world",
                        "logo": logos[0].get("href") if logos else None,
                    },
                    "venue": {},
                }
            )
        if not teams:
            raise EspnFootballError(f"ESPN returned no teams for {competition.name}")
        return teams

    def squad(self, team_id: str) -> list[dict[str, Any]]:
        if not team_id.startswith("espn_"):
            raise EspnFootballError(f"Invalid ESPN team ID: {team_id}")
        league_code, source_id = team_id.removeprefix("espn_").rsplit("_", 1)
        payload = self._get(
            f"/apis/site/v2/sports/soccer/{league_code}/teams/{source_id}/roster"
        )
        athletes = payload.get("athletes")
        if not isinstance(athletes, list) or not athletes:
            published_season = int(
                (payload.get("season") or {}).get("year") or date.today().year
            )
            athletes = self._core_squad(league_code, source_id, published_season)
        return self._normalize_athletes(team_id, athletes)

    def _core_squad(
        self, league_code: str, source_id: str, published_season: int
    ) -> list[dict[str, Any]]:
        for season in (published_season, published_season - 1):
            payload = self._get(
                "https://sports.core.api.espn.com/v2/sports/soccer/leagues/"
                f"{league_code}/seasons/{season}/teams/{source_id}/athletes"
                "?active=true&limit=100"
            )
            references = payload.get("items") or []
            if not references:
                continue
            athletes: list[dict[str, Any]] = []
            for item in references:
                reference = str(item.get("$ref", ""))
                if not reference:
                    continue
                public_reference = reference.replace("http://", "https://").replace(
                    "sports.core.api.espn.pvt", "sports.core.api.espn.com"
                )
                athletes.append(self._get(public_reference))
            if athletes:
                return athletes
        return []

    @staticmethod
    def _normalize_athletes(
        team_id: str, athletes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        players: list[dict[str, Any]] = []
        for athlete in athletes:
            source_player_id = str(athlete.get("id", "")).strip()
            if not source_player_id:
                continue
            position = athlete.get("position") or {}
            headshot = athlete.get("headshot") or {}
            players.append(
                {
                    "id": f"espn_{source_player_id}",
                    "source": "espn",
                    "sourceId": source_player_id,
                    "name": athlete.get("fullName") or athlete.get("displayName"),
                    "firstname": athlete.get("firstName"),
                    "lastname": athlete.get("lastName"),
                    "age": athlete.get("age"),
                    "nationality": athlete.get("citizenship")
                    or (athlete.get("birthPlace") or {}).get("country"),
                    "dateOfBirth": athlete.get("dateOfBirth"),
                    "position": position.get("displayName") or position.get("name"),
                    "photo": headshot.get("href"),
                }
            )
        if not players:
            raise EspnFootballError(f"ESPN returned no valid players for team {team_id}")
        return players

    def season_start(self, competition_id: int, season: int) -> date | None:
        competition = COMPETITIONS_BY_ID[competition_id]
        response = httpx.get(
            "https://sports.core.api.espn.com/v2/sports/soccer/leagues/"
            f"{competition.espn_code}/seasons/{season}?lang=en&region=us",
            timeout=self._client.timeout,
            headers={"User-Agent": "GoalioMasterData/1.0"},
        )
        response.raise_for_status()
        start_date = response.json().get("startDate")
        return date.fromisoformat(start_date[:10]) if start_date else None


class FallbackFootballClient:
    def __init__(self, primary: EspnFootballClient, fallback: ApiFootballClient):
        self.primary = primary
        self.fallback = fallback

    def close(self) -> None:
        self.primary.close()
        self.fallback.close()

    def teams(self, competition_id: int, season: int) -> list[dict[str, Any]]:
        try:
            return self.primary.teams(competition_id, season)
        except (EspnFootballError, httpx.HTTPError, KeyError, ValueError) as error:
            print(f"ESPN unavailable, using API-Football fallback: {error}")
            return self.fallback.teams(competition_id, season)

    def squad(self, team_id: str) -> list[dict[str, Any]]:
        if team_id.startswith("espn_"):
            return self.primary.squad(team_id)
        return self.fallback.squad(team_id)

    def season_start(self, competition_id: int, season: int) -> date | None:
        try:
            return self.primary.season_start(competition_id, season)
        except (EspnFootballError, httpx.HTTPError, KeyError, ValueError):
            return self.fallback.season_start(competition_id, season)


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
        status = data.get("status", "in_progress")
        team_ids = data.get("failedTeamIds", []) if status == "incomplete" else data.get("teamIds", [])
        return SyncState(
            team_ids=tuple(str(team_id) for team_id in team_ids),
            next_team_index=0 if status == "incomplete" else int(data.get("nextTeamIndex", 0)),
            status=status,
        )

    def _remove_mappings(self, mappings: list[Any]) -> None:
        removed_teams_by_player: dict[str, set[str]] = {}
        for mapping in mappings:
            data = mapping.to_dict()
            if data.get("playerId") is not None and data.get("teamId") is not None:
                removed_teams_by_player.setdefault(str(data["playerId"]), set()).add(
                    str(data["teamId"])
                )

        player_refs = {
            player_id: self.client.collection("players").document(str(player_id))
            for player_id in removed_teams_by_player
        }
        player_snapshots = (
            {
                str(snapshot.id): snapshot
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
                str(team_id)
                for team_id in data.get("team_ids", [])
                if str(team_id) not in removed_team_ids
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

    def _deactivate_old_teams(self, competition_id: int, current_team_ids: set[str]) -> None:
        old_teams = self.client.collection("teams").where(
            filter=FieldFilter("competition_ids", "array_contains", competition_id)
        ).stream()
        batch = self.client.batch()
        operation_count = 0
        for snapshot in old_teams:
            team_id = str(snapshot.id)
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
        team_ids = [str(item["team"]["id"]) for item in teams]
        batch = self.client.batch()
        for item in teams:
            team = item.get("team", {})
            venue = item.get("venue") or {}
            team_id = str(team["id"])
            batch.set(
                self.client.collection("teams").document(str(team_id)),
                {
                    "id": team_id,
                    "source": team.get("source", "api-football"),
                    "source_id": str(team.get("sourceId", team_id)),
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
                "provider": (
                    teams[0].get("team", {}).get("source", "api-football")
                    if teams
                    else None
                ),
                "season": season,
                "teamIds": team_ids,
                "nextTeamIndex": 0,
                "failedTeamIds": [],
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
        team_id: str,
        players: list[dict[str, Any]],
    ) -> None:
        new_player_ids = {str(player["id"]) for player in players}
        existing_mappings = list(
            self.client.collection("team_players")
            .where(filter=FieldFilter("teamId", "==", team_id))
            .stream()
        )
        obsolete_mappings = [
            mapping
            for mapping in existing_mappings
            if str(mapping.to_dict().get("playerId", "")) not in new_player_ids
        ]
        self._remove_mappings(obsolete_mappings)

        batch = self.client.batch()
        for player in players:
            player_id = str(player["id"])
            position = player.get("position")
            batch.set(
                self.client.collection("players").document(str(player_id)),
                {
                    "id": player_id,
                    "source": player.get("source", "api-football"),
                    "source_id": str(player.get("sourceId", player_id)),
                    "name": player.get("name"),
                    "firstname": player.get("firstname"),
                    "lastname": player.get("lastname"),
                    "age": player.get("age"),
                    "nationality": player.get("nationality"),
                    "date_of_birth": player.get("dateOfBirth"),
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

    def record_failure(
        self, competition_id: int, season: int, team_id: str, error: str
    ) -> None:
        self.client.collection("master_data_sync").document(
            self._state_id(competition_id, season)
        ).update(
            {
                "failedTeamIds": firestore.ArrayUnion([team_id]),
                "lastError": error[:500],
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }
        )

    def clear_failure(self, competition_id: int, season: int, team_id: str) -> None:
        self.client.collection("master_data_sync").document(
            self._state_id(competition_id, season)
        ).update(
            {
                "failedTeamIds": firestore.ArrayRemove([team_id]),
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }
        )

    def complete(self, competition_id: int, season: int) -> bool:
        state_ref = self.client.collection("master_data_sync").document(
            self._state_id(competition_id, season)
        )
        state = state_ref.get().to_dict()
        failed_team_ids = state.get("failedTeamIds", [])
        if failed_team_ids:
            state_ref.update(
                {
                    "status": "incomplete",
                    "nextTeamIndex": 0,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                }
            )
            return False
        current_team_ids = {str(team_id) for team_id in state.get("teamIds", [])}
        competition_mappings = self.client.collection("team_players").where(
            filter=FieldFilter("competitionId", "==", competition_id)
        ).stream()
        obsolete_mappings = [
            mapping
            for mapping in competition_mappings
            if str(mapping.to_dict().get("teamId", "")) not in current_team_ids
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
        return True


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
            requests_used += 1
            try:
                players = self.api.squad(team_id)
                self.store.replace_team_squad(competition, season, team_id, players)
                self.store.clear_failure(competition.id, season, team_id)
            except (ApiFootballError, EspnFootballError, httpx.HTTPError) as error:
                self.store.record_failure(
                    competition.id, season, team_id, str(error)
                )
            next_index += 1
            teams_processed += 1
            self.store.advance(competition.id, season, next_index)

        completed = next_index >= len(state.team_ids)
        if completed:
            completed = self.store.complete(competition.id, season)
        return SyncResult(
            competition.id,
            season,
            requests_used,
            teams_processed,
            completed,
        )
