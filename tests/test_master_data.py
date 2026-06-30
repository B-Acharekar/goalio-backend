from datetime import date
from typing import Any

from app.repositories.football import normalize_search, search_terms
from app.services.master_data import (
    COMPETITIONS_BY_ID,
    EspnFootballClient,
    EspnFootballError,
    MasterDataSyncService,
    SyncState,
    is_sync_due,
)


class FakeApi:
    def __init__(self):
        self.team_calls = 0
        self.teams_by_competition = {
            39: [{"team": {"id": 10, "name": "Club One"}}, {"team": {"id": 11, "name": "Club Two"}}],
            1: [{"team": {"id": 20, "name": "Country One"}}],
        }
        shared_player = {"id": 276, "name": "Lionel Messi", "position": "Attacker"}
        self.squads = {
            "10": [shared_player],
            "11": [{"id": 999, "name": "Other Player", "position": "Defender"}],
            "20": [shared_player],
        }

    def teams(self, competition_id: int, season: int) -> list[dict[str, Any]]:
        self.team_calls += 1
        return self.teams_by_competition[competition_id]

    def squad(self, team_id: str) -> list[dict[str, Any]]:
        return self.squads[team_id]


class FakeStore:
    def __init__(self):
        self.states: dict[tuple[int, int], SyncState] = {}
        self.players: dict[str, set[str]] = {}
        self.failures: dict[tuple[int, int], set[str]] = {}

    def load_state(self, competition_id: int, season: int) -> SyncState | None:
        return self.states.get((competition_id, season))

    def initialize_competition(self, competition, season, teams):
        state = SyncState(tuple(str(item["team"]["id"]) for item in teams), 0, "in_progress")
        self.states[(competition.id, season)] = state
        return state

    def replace_team_squad(self, competition, season, team_id, players):
        for player in players:
            self.players.setdefault(str(player["id"]), set()).add(str(team_id))

    def advance(self, competition_id, season, next_team_index):
        state = self.states[(competition_id, season)]
        self.states[(competition_id, season)] = SyncState(
            state.team_ids, next_team_index, state.status
        )

    def record_failure(self, competition_id, season, team_id, error):
        self.failures.setdefault((competition_id, season), set()).add(team_id)

    def clear_failure(self, competition_id, season, team_id):
        self.failures.setdefault((competition_id, season), set()).discard(team_id)

    def complete(self, competition_id, season):
        if self.failures.get((competition_id, season)):
            return False
        state = self.states[(competition_id, season)]
        self.states[(competition_id, season)] = SyncState(
            state.team_ids, state.next_team_index, "completed"
        )
        return True


def test_sync_resumes_with_request_budget_and_deduplicates_players():
    api = FakeApi()
    store = FakeStore()
    sync = MasterDataSyncService(api, store)

    first = sync.sync_competition(COMPETITIONS_BY_ID[39], 2026, max_requests=2)
    assert first.requests_used == 2
    assert first.teams_processed == 1
    assert first.completed is False

    second = sync.sync_competition(COMPETITIONS_BY_ID[39], 2026, max_requests=1)
    assert second.completed is True
    assert api.team_calls == 1

    country = sync.sync_competition(COMPETITIONS_BY_ID[1], 2026, max_requests=2)
    assert country.completed is True
    assert store.players["276"] == {"10", "20"}
    assert len(store.players) == 2


def test_seven_day_sync_window():
    assert is_sync_due(date(2026, 8, 8), date(2026, 8, 1))
    assert not is_sync_due(date(2026, 8, 8), date(2026, 7, 31))
    assert is_sync_due(date(2026, 8, 8), date(2026, 8, 22))
    assert not is_sync_due(date(2026, 8, 8), date(2026, 8, 23))


def test_unavailable_team_is_recorded_without_blocking_competition():
    class PartiallyUnavailableApi(FakeApi):
        def squad(self, team_id: str) -> list[dict[str, Any]]:
            if team_id == "10":
                raise EspnFootballError("roster not published")
            return super().squad(team_id)

    store = FakeStore()
    result = MasterDataSyncService(PartiallyUnavailableApi(), store).sync_competition(
        COMPETITIONS_BY_ID[39], 2026, max_requests=3
    )

    assert result.completed is False
    assert store.failures[(39, 2026)] == {"10"}
    assert store.players["999"] == {"11"}


def test_search_terms_are_case_and_accent_insensitive():
    assert normalize_search("  Kylian Mbappé ") == "kylian mbappe"
    terms = search_terms("Kylian Mbappé")
    assert "kyl" in terms
    assert "mba" in terms
    assert "kylian m" in terms


def test_espn_client_normalizes_team_and_roster(monkeypatch):
    client = EspnFootballClient(request_interval_seconds=0)

    def fake_get(path: str):
        if path.endswith("/teams"):
            return {
                "sports": [{"leagues": [{"season": {"year": 2026}, "teams": [{"team": {
                    "id": "349",
                    "displayName": "AFC Bournemouth",
                    "abbreviation": "BOU",
                    "logos": [{"href": "https://example.com/team.png"}],
                }}]}]}]
            }
        return {
            "athletes": [{
                "id": "93193",
                "firstName": "Fraser",
                "lastName": "Forster",
                "fullName": "Fraser Forster",
                "age": 38,
                "dateOfBirth": "1988-03-17T08:00Z",
                "citizenship": "England",
                "position": {"displayName": "Goalkeeper"},
                "headshot": {"href": "https://example.com/player.png"},
            }]
        }

    monkeypatch.setattr(client, "_get", fake_get)
    teams = client.teams(39, 2026)
    players = client.squad("espn_eng.1_349")
    client.close()

    assert teams[0]["team"]["id"] == "espn_eng.1_349"
    assert teams[0]["team"]["source"] == "espn"
    assert players[0]["id"] == "espn_93193"
    assert players[0]["dateOfBirth"] == "1988-03-17T08:00Z"
    assert players[0]["nationality"] == "England"


def test_espn_client_uses_previous_core_season_for_empty_roster(monkeypatch):
    client = EspnFootballClient(request_interval_seconds=0)

    def fake_get(path: str):
        if path.endswith("/roster"):
            return {"season": {"year": 2026}, "athletes": []}
        if "/seasons/2026/" in path:
            return {"items": []}
        if "/seasons/2025/teams/" in path:
            return {"items": [{"$ref": "http://sports.core.api.espn.pvt/athlete/1"}]}
        return {
            "id": "143100",
            "firstName": "Pietro",
            "lastName": "Terracciano",
            "fullName": "Pietro Terracciano",
            "citizenship": "Italy",
            "position": {"displayName": "Goalkeeper"},
        }

    monkeypatch.setattr(client, "_get", fake_get)
    players = client.squad("espn_ita.1_103")
    client.close()

    assert players[0]["id"] == "espn_143100"
    assert players[0]["position"] == "Goalkeeper"
