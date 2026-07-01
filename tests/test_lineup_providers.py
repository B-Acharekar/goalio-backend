from datetime import datetime, timezone

from app.schemas.lineups import MatchLineupResponse, NormalizedTeamLineup
from app.schemas.matches import LineupPlayer, MatchDetail, MatchTeam, TeamLineup
from app.services.lineup_providers.base import MatchMeta, ProviderResult
from app.services.lineup_providers.thesportsdb import (
    TheSportsDbProvider, event_search_names, normalize_name, parse_lineup_payload, score_candidate,
)
from app.services.lineups import CachedLineup, LineupService


def meta():
    return MatchMeta("760495", "fifa.world", "England", "Congo DR", "448", "2850", None, None,
                     datetime(2026, 7, 1, 16, tzinfo=timezone.utc), "Scheduled")


def detail(lineups=None):
    return MatchDetail(matchId="760495", league="fifa.world", kickoff="2026-07-01T16:00:00Z",
                       status="Scheduled", homeTeam=MatchTeam(id="448", name="England"),
                       awayTeam=MatchTeam(id="2850", name="Congo DR"), lineups=lineups or [])


def team(name, team_id, starters=1, bench=0):
    return TeamLineup(teamId=team_id, teamName=name,
                      starters=[LineupPlayer(name=f"{name} Starter {i}", starter=True) for i in range(starters)],
                      substitutes=[LineupPlayer(name=f"{name} Bench {i}", substitute=True) for i in range(bench)])


class Client:
    def __init__(self, value): self.value = value; self.loaded = False
    def espn_detail(self, league, event_id): self.loaded = True; return self.value


class Store:
    def __init__(self, cached=None): self.cached = cached; self.written = None
    def get(self, event_id): return self.cached
    def write(self, response, attempts, content_hash): self.written = response


class Provider:
    def __init__(self, result): self.result = result; self.calls = []
    def fetch(self, value): self.calls.append(value); return self.result


def test_espn_complete_lineup_skips_thesportsdb():
    provider = Provider(ProviderResult())
    response = LineupService(Client(detail([team("England", "448", 11), team("Congo DR", "2850", 11)])), Store(), provider).get("fifa.world", "760495", True)
    assert response.source == "espn"
    assert not provider.calls


def test_empty_espn_uses_thesportsdb_and_passes_espn_meta():
    provider = Provider(ProviderResult([team("England", "448", 11), team("Congo DR", "2850", 11)]))
    response = LineupService(Client(detail()), Store(), provider).get("fifa.world", "760495", True)
    assert response.source == "theSportsDb"
    assert provider.calls[0].home_team == "England"
    assert len(response.home.startingXI) == 11


def test_roster_without_starter_markers_falls_back():
    roster = [TeamLineup(teamId="448", teamName="England", substitutes=[LineupPlayer(name="Squad Player")])]
    provider = Provider(ProviderResult([team("England", "448"), team("Congo DR", "2850")]))
    LineupService(Client(detail(roster)), Store(), provider).get("fifa.world", "760495", True)
    assert len(provider.calls) == 1


def test_generated_empty_cache_does_not_block_provider_refresh():
    empty = MatchLineupResponse(eventId="760495", status="NOT_AVAILABLE", source="generated", formationStatus="UNKNOWN",
                                lastUpdated=datetime.now(timezone.utc), nextRefreshAt=datetime(2099, 1, 1, tzinfo=timezone.utc),
                                home=NormalizedTeamLineup(), away=NormalizedTeamLineup())
    provider = Provider(ProviderResult([team("England", "448"), team("Congo DR", "2850")]))
    response = LineupService(Client(detail()), Store(CachedLineup(empty)), provider).get("fifa.world", "760495")
    assert response.source == "theSportsDb"


def test_empty_both_providers_is_not_available():
    response = LineupService(Client(detail()), Store(), Provider(ProviderResult())).get("fifa.world", "760495", True)
    assert (response.source, response.status) == ("generated", "NOT_AVAILABLE")


def test_alias_and_reversed_candidate_scoring():
    direct = score_candidate({"idEvent": "1", "strHomeTeam": "England", "strAwayTeam": "DR Congo", "dateEvent": "2026-07-01", "strLeague": "FIFA World Cup"}, meta())
    reverse = score_candidate({"idEvent": "2", "strHomeTeam": "Democratic Republic of Congo", "strAwayTeam": "England", "dateEvent": "2026-07-01"}, meta())
    assert direct and direct.score >= .95 and not direct.reversed
    assert reverse and reverse.reversed and reverse.score >= .9


def test_search_names_and_normalization_cover_congo_aliases():
    assert normalize_name("Côte-d'Ivoire") == "cote d ivoire"
    assert "England_vs_DR Congo" in event_search_names("England", "Congo DR")


def test_v1_lineup_parser_extracts_starters_and_bench():
    payload = {"lineup": [{"strPlayer": "Harry Kane", "strTeam": "England", "strPosition": "ST", "strSubstitute": "No"},
                          {"strPlayer": "Cole Palmer", "strTeam": "England", "strSubstitute": "Yes"}]}
    parsed = parse_lineup_payload(payload, meta())
    assert parsed[0].starters[0].name == "Harry Kane"
    assert parsed[0].substitutes[0].name == "Cole Palmer"


def test_v1_national_lineup_uses_home_marker_when_team_is_club():
    payload = {"lineup": [{"strPlayer": "Jordan Pickford", "strTeam": "Everton", "strHome": "Yes", "strSubstitute": "No"},
                          {"strPlayer": "Congo Keeper", "strTeam": "Some Club", "strHome": "No", "strSubstitute": "No"}]}
    parsed = parse_lineup_payload(payload, meta())
    assert parsed[0].starters[0].name == "Jordan Pickford"
    assert parsed[1].starters[0].name == "Congo Keeper"


def test_v2_separated_lineup_parser_and_reversal():
    payload = {"event_lineup": {"home": [{"strPlayerName": "Player E", "type": "starting"}],
                                 "away": [{"strPlayerName": "Player C", "type": "starting"}]}}
    normal = parse_lineup_payload(payload, meta())
    reversed_result = parse_lineup_payload(payload, meta(), reversed_=True)
    assert normal[0].starters[0].name == "Player E"
    assert reversed_result[1].starters[0].name == "Player E"


def test_debug_urls_mask_api_key(monkeypatch):
    class Mappings:
        def get(self, event_id): return {"providerEventId": "99", "reversed": False}
        def write(self, event_id, mapping): pass
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"lineup": []}
    monkeypatch.setattr("app.services.lineup_providers.thesportsdb.httpx.get", lambda *args, **kwargs: Response())
    result = TheSportsDbProvider("secret-key", "https://example.test", False, Mappings()).fetch(meta())
    assert all("secret-key" not in attempt.get("url", "") for attempt in result.attempts)
    assert any("/***/" in attempt.get("url", "") for attempt in result.attempts)
