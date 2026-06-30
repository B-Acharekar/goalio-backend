from datetime import date
from typing import Any

from app.repositories.football import normalize_search, search_terms
from app.services.master_data import (
    COMPETITIONS_BY_ID,
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
            10: [shared_player],
            11: [{"id": 999, "name": "Other Player", "position": "Defender"}],
            20: [shared_player],
        }

    def teams(self, competition_id: int, season: int) -> list[dict[str, Any]]:
        self.team_calls += 1
        return self.teams_by_competition[competition_id]

    def squad(self, team_id: int) -> list[dict[str, Any]]:
        return self.squads[team_id]


class FakeStore:
    def __init__(self):
        self.states: dict[tuple[int, int], SyncState] = {}
        self.players: dict[int, set[int]] = {}

    def load_state(self, competition_id: int, season: int) -> SyncState | None:
        return self.states.get((competition_id, season))

    def initialize_competition(self, competition, season, teams):
        state = SyncState(tuple(int(item["team"]["id"]) for item in teams), 0, "in_progress")
        self.states[(competition.id, season)] = state
        return state

    def replace_team_squad(self, competition, season, team_id, players):
        for player in players:
            self.players.setdefault(int(player["id"]), set()).add(team_id)

    def advance(self, competition_id, season, next_team_index):
        state = self.states[(competition_id, season)]
        self.states[(competition_id, season)] = SyncState(
            state.team_ids, next_team_index, state.status
        )

    def complete(self, competition_id, season):
        state = self.states[(competition_id, season)]
        self.states[(competition_id, season)] = SyncState(
            state.team_ids, state.next_team_index, "completed"
        )


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
    assert store.players[276] == {10, 20}
    assert len(store.players) == 2


def test_seven_day_sync_window():
    assert is_sync_due(date(2026, 8, 8), date(2026, 8, 1))
    assert not is_sync_due(date(2026, 8, 8), date(2026, 7, 31))
    assert is_sync_due(date(2026, 8, 8), date(2026, 8, 22))
    assert not is_sync_due(date(2026, 8, 8), date(2026, 8, 23))


def test_search_terms_are_case_and_accent_insensitive():
    assert normalize_search("  Kylian Mbappé ") == "kylian mbappe"
    terms = search_terms("Kylian Mbappé")
    assert "kyl" in terms
    assert "mba" in terms
    assert "kylian m" in terms
