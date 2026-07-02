from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.dependencies import (
    CurrentUser,
    get_current_user,
    get_football_repository,
    get_football_data_provider,
    get_lineup_store,
    get_match_detail_client,
    get_match_detail_store,
    get_profile_repository,
    get_scoreboard_store,
    get_thesportsdb_provider,
    get_quiz_repository,
)
from app.main import app
from app.schemas.football import PlayerPage, PlayerResult, TeamPage, TeamResult
from app.schemas.matches import MatchTeam, ScoreboardMatch
from app.schemas.profile import ProfileUpsert, UserProfile
from app.services.worldcup import _bracket
from app.services.lineups import CachedLineup
from app.services.lineup_providers.base import ProviderResult
from app.schemas.quiz import LeaderboardEntry, QuizAnswerResult, QuizLeaderboard
from app.services.quiz import QUESTION_BY_ID


class MemoryProfiles:
    def __init__(self):
        self.profiles: dict[str, UserProfile] = {}

    def get(self, uid: str) -> UserProfile | None:
        return self.profiles.get(uid)

    def upsert(self, uid: str, profile: ProfileUpsert) -> UserProfile:
        now = datetime.now(UTC)
        team_names = {"6": "Brazil", "26": "Argentina"}
        player_names = {"154": "Lionel Messi", "276": "Neymar"}
        saved = UserProfile(
            userId=uid,
            **profile.model_dump(),
            favoriteTeams=[team_names[item] for item in profile.favoriteTeamIds],
            favoritePlayers=[player_names[item] for item in profile.favoritePlayerIds],
            createdAt=self.profiles.get(uid, None).createdAt if uid in self.profiles else now,
            updatedAt=now,
            profileCompleted=True,
        )
        self.profiles[uid] = saved
        return saved

    def is_username_available(self, username: str, uid: str) -> bool:
        return all(profile.username != username or profile.userId == uid for profile in self.profiles.values())

    def profile_login(self, name: str, username: str) -> str:
        profile = next((item for item in self.profiles.values() if item.username == username.strip().lower() and item.name.casefold() == " ".join(name.split()).casefold()), None)
        if profile is None:
            from fastapi import HTTPException
            raise HTTPException(401, "Full name or username did not match")
        return f"token-for-{profile.userId}"

    def profile_matches(self, name: str, username: str) -> bool:
        return any(item.username == username.strip().lower() and item.name.casefold() == " ".join(name.split()).casefold() for item in self.profiles.values())


class MemoryFootball:
    def list_teams(self, limit: int, cursor: str | None, competition_id: int | None = None) -> TeamPage:
        return TeamPage(items=[TeamResult(id="6", name="Brazil", shortName="BRA", competitionIds=[1])])

    def list_players(self, limit: int, cursor: str | None, competition_id: int | None = None) -> PlayerPage:
        return PlayerPage(items=[PlayerResult(id="154", name="Lionel Messi", team="Argentina", competitionIds=[1])])

    def search_teams(self, query: str, limit: int, cursor: str | None) -> TeamPage:
        teams = [TeamResult(id="6", name="Brazil", shortName="BRA", competitionIds=[1])]
        return TeamPage(items=[team for team in teams if query.casefold() in team.name.casefold()][:limit])

    def search_players(self, query: str, limit: int, cursor: str | None) -> PlayerPage:
        players = [PlayerResult(id="154", name="Lionel Messi", team="Argentina", competitionIds=[1])]
        return PlayerPage(items=[player for player in players if query.casefold() in player.name.casefold()][:limit])


class MemoryMatchDetail:
    def __init__(self):
        self.documents = {}

    def get(self, league: str, event_id: str):
        return self.documents.get((league, event_id))

    def is_due(self, league: str, event_id: str):
        return (league, event_id) not in self.documents

    def write_if_changed(self, detail):
        self.documents[(detail.league, detail.matchId)] = detail
        return True

    def cached_detail(self, league: str, event_id: str, store):
        cached = store.get(league, event_id)
        if cached is not None and not store.is_due(league, event_id):
            return cached
        detail = self.detail(league, event_id)
        store.write_if_changed(detail)
        return detail

    def schedule(
        self,
        league: str,
        date: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ):
        from app.services.match_detail import normalize_espn_scoreboard, schedule_dates_to_espn

        schedule_dates_to_espn(date, from_date, to_date)
        return normalize_espn_scoreboard(
            league,
            self._scoreboard_payload(),
            schedule_date=date or (f"{from_date}/{to_date}" if from_date and to_date else None),
        )

    def scoreboard(self, league: str, dates: str | None = None):
        from app.services.match_detail import normalize_espn_scoreboard

        return normalize_espn_scoreboard(league, self._scoreboard_payload())

    def standings(self, league: str, season: int | None = None):
        from app.services.match_detail import normalize_espn_standings

        return normalize_espn_standings(
            league,
            {
                "standings": [
                    {
                        "name": "Group A",
                        "entries": [
                            {
                                "team": {"id": "481", "displayName": "Germany", "abbreviation": "GER"},
                                "stats": [
                                    {"name": "rank", "value": 1},
                                    {"name": "points", "value": 6},
                                    {"name": "gamesPlayed", "value": 2},
                                    {"name": "wins", "value": 2},
                                    {"name": "goalsFor", "value": 5},
                                    {"name": "goalsAgainst", "value": 1},
                                    {"name": "goalDifference", "value": 4},
                                ],
                            }
                        ],
                    }
                ]
            },
            season,
        )

    def _scoreboard_payload(self):
        return {
            "events": [
                {
                    "id": "760422",
                    "name": "Germany vs Curacao",
                    "shortName": "GER v CUW",
                    "date": "2026-06-14T17:00Z",
                    "competitions": [
                        {
                            "date": "2026-06-14T17:00Z",
                            "status": {
                                "type": {
                                    "abbreviation": "FT",
                                    "detail": "Full Time",
                                    "description": "Full Time",
                                    "state": "post",
                                }
                            },
                            "venue": {
                                "fullName": "Goalio Stadium",
                                "address": {"city": "Berlin"},
                            },
                            "officials": [
                                {"displayName": "P. Sampaio", "role": "Referee"}
                            ],
                            "weather": {
                                "displayValue": "28°C Clear",
                                "condition": "Clear",
                            },
                            "competitors": [
                                {
                                    "homeAway": "home",
                                    "score": "7",
                                    "team": {
                                        "id": "481",
                                        "displayName": "Germany",
                                        "shortDisplayName": "Germany",
                                        "abbreviation": "GER",
                                    },
                                },
                                {
                                    "homeAway": "away",
                                    "score": "1",
                                    "team": {
                                        "id": "11678",
                                        "displayName": "Curacao",
                                        "shortDisplayName": "Curacao",
                                        "abbreviation": "CUW",
                                    },
                                },
                            ],
                        }
                    ],
                }
            ]
        }

    def detail(self, league: str, event_id: str):
        from app.services.match_detail import normalize_espn_summary

        return normalize_espn_summary(
            league,
            event_id,
            {
                "header": {
                    "competitions": [
                        {
                            "date": "2026-06-14T17:00Z",
                            "status": {
                                "type": {
                                    "abbreviation": "FT",
                                    "detail": "Full Time",
                                    "description": "Full Time",
                                }
                            },
                            "venue": {
                                "fullName": "Goalio Stadium",
                                "address": {"city": "Berlin"},
                            },
                            "officials": [
                                {"displayName": "P. Sampaio", "role": "Referee"}
                            ],
                            "weather": {
                                "displayValue": "28°C Clear",
                                "condition": "Clear",
                            },
                            "competitors": [
                                {
                                    "homeAway": "home",
                                    "score": "7",
                                    "team": {
                                        "id": "481",
                                        "displayName": "Germany",
                                        "shortDisplayName": "Germany",
                                        "abbreviation": "GER",
                                        "logo": "https://example.com/ger.png",
                                    },
                                },
                                {
                                    "homeAway": "away",
                                    "score": "1",
                                    "team": {
                                        "id": "11678",
                                        "displayName": "Curacao",
                                        "shortDisplayName": "Curacao",
                                        "abbreviation": "CUW",
                                    },
                                },
                            ],
                            "details": [
                                {
                                    "text": "Nico Schlotterbeck Goal - Header",
                                    "type": {"text": "Goal - Header"},
                                    "time": {"displayValue": "38'"},
                                    "team": {"displayName": "Germany"},
                                }
                            ],
                        }
                    ]
                },
                "boxscore": {
                    "teams": [
                        {
                            "team": {"id": "481"},
                            "formation": "4-2-3-1",
                            "coach": {"displayName": "Julian Nagelsmann"},
                            "athletes": [
                                {
                                    "starter": True,
                                    "athlete": {
                                        "id": "1",
                                        "displayName": "Manuel Neuer",
                                        "jersey": "1",
                                        "position": {"abbreviation": "GK"},
                                    },
                                },
                                {
                                    "substitute": True,
                                    "athlete": {
                                        "id": "2",
                                        "displayName": "Marc-Andre ter Stegen",
                                        "jersey": "22",
                                        "position": {"abbreviation": "GK"},
                                    },
                                },
                            ],
                            "statistics": [
                                {
                                    "name": "possessionPct",
                                    "displayName": "Possession",
                                    "displayValue": "65%",
                                }
                            ],
                        }
                    ]
                },
                "leaders": [
                    {
                        "displayName": "Shots",
                        "leaders": [
                            {
                                "displayValue": "4",
                                "athlete": {
                                    "id": "231182",
                                    "displayName": "Kai Havertz",
                                    "position": {"displayName": "Forward"},
                                    "jersey": "7",
                                    "links": [
                                        {
                                            "href": "https://www.espn.com/soccer/player/_/id/231182/kai-havertz"
                                        }
                                    ],
                                },
                                "statistics": [
                                    {
                                        "name": "totalShots",
                                        "displayName": "Shots",
                                        "displayValue": "4",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "commentary": [{"text": "Final whistle"}],
                "article": {"story": "Germany won comfortably."},
            },
        )

    def espn_detail(self, league: str, event_id: str):
        return self.detail(league, event_id)

    def espn_lineups(self, league: str, event_id: str):
        return self.detail(league, event_id).lineups

    def google_lineups(self, league: str, event_id: str, detail=None):
        return []

    def yahoo_lineups(self, league: str, event_id: str, detail=None):
        return []

    def cached_schedule(self, league, store, date=None, from_date=None, to_date=None, force=False):
        from app.services.match_detail import schedule_dates_to_espn, scoreboard_cache_key
        dates = schedule_dates_to_espn(date, from_date, to_date)
        key = scoreboard_cache_key(league, dates)
        cached = store.get(key, 120)
        if cached is not None and not force:
            return cached
        response = self.schedule(league, date=date, from_date=from_date, to_date=to_date)
        store.write(key, response)
        return response


class MemoryLineupStore:
    def __init__(self):
        self.documents = {}

    def get(self, event_id: str):
        return self.documents.get(event_id)

    def write(self, response, attempts, content_hash):
        self.documents[response.eventId] = CachedLineup(response, content_hash)


class MemoryScoreboardStore:
    def __init__(self):
        self.documents = {}

    def get(self, cache_key, max_age_seconds):
        return self.documents.get(cache_key)

    def write(self, cache_key, response):
        self.documents[cache_key] = response


class EmptyTheSportsDb:
    def fetch(self, meta):
        return ProviderResult(attempts=[{"provider": "theSportsDb", "step": "test", "success": False}])


class MemoryQuiz:
    def __init__(self): self.sessions = {}; self.xp = {}
    def create(self, uid, question_ids, now):
        sid = f"session-{len(self.sessions)}"; self.sessions[sid] = [uid, question_ids, 0, now]; return sid
    def answer(self, uid, session_id, question_id, answer_index, now):
        session = self.sessions[session_id]; question = QUESTION_BY_ID[question_id]; correct = answer_index == question[4]
        delta = 10 if correct else -5; self.xp[uid] = max(0, self.xp.get(uid, 0) + delta); session[2] += 1
        return QuizAnswerResult(correct=correct, timedOut=False, correctAnswerIndex=question[4], explanation=question[5], xpDelta=delta, totalXp=self.xp[uid], currentQuestion=session[2], completed=session[2] == 5, questionStartedAt=None if session[2] == 5 else now)
    def leaderboard(self, uid, limit):
        entry = LeaderboardEntry(rank=1, username="aegies", xp=self.xp.get(uid, 0), userId=uid)
        return QuizLeaderboard(entries=[entry], me=entry)


repository = MemoryProfiles()
app.dependency_overrides[get_current_user] = lambda: CurrentUser("test-user")
app.dependency_overrides[get_profile_repository] = lambda: repository
app.dependency_overrides[get_football_repository] = lambda: MemoryFootball()
app.dependency_overrides[get_match_detail_client] = lambda: MemoryMatchDetail()
app.dependency_overrides[get_match_detail_store] = lambda: MemoryMatchDetail()
app.dependency_overrides[get_lineup_store] = lambda: MemoryLineupStore()
app.dependency_overrides[get_scoreboard_store] = lambda: MemoryScoreboardStore()
app.dependency_overrides[get_thesportsdb_provider] = lambda: EmptyTheSportsDb()
app.dependency_overrides[get_football_data_provider] = lambda: EmptyTheSportsDb()
quiz_repository = MemoryQuiz()
app.dependency_overrides[get_quiz_repository] = lambda: quiz_repository
client = TestClient(app)


def test_profile_round_trip_and_personalized_home():
    payload = {
        "name": "Aegies User",
        "username": "aegies",
        "favoriteTeamIds": ["6", "26"],
        "favoritePlayerIds": ["154", "276"],
        "onboardingCompleted": True,
    }
    created = client.post("/api/v1/users/profile", json=payload)
    assert created.status_code == 200
    assert created.json()["userId"] == "test-user"
    assert created.json()["profileCompleted"] is True
    assert created.json()["favoriteTeamIds"] == ["6", "26"]

    loaded = client.get("/api/v1/users/profile")
    assert loaded.status_code == 200
    assert loaded.json()["favoriteTeams"] == ["Brazil", "Argentina"]

    home = client.get("/api/v1/home")
    assert home.status_code == 200
    assert home.json()["greeting"] == "Welcome back, Aegies"
    login = client.post("/api/v1/auth/profile-login", json={"name": "Aegies User", "username": "aegies"})
    assert login.status_code == 200
    assert login.json()["customToken"] == "token-for-test-user"
    denied = client.post("/api/v1/auth/profile-login", json={"name": "Wrong Person", "username": "aegies"})
    assert denied.status_code == 401
    matched = client.post("/api/v1/users/profile/identity-match", json={"name": "Aegies User", "username": "aegies"})
    assert matched.status_code == 200 and matched.json() == {"matched": True}
    mismatch = client.post("/api/v1/users/profile/identity-match", json={"name": "Wrong Person", "username": "aegies"})
    assert mismatch.status_code == 200 and mismatch.json() == {"matched": False}


def test_search_teams_and_players():
    all_teams = client.get("/api/v1/football/teams")
    assert all_teams.status_code == 200
    assert all_teams.json()["items"][0]["name"] == "Brazil"
    assert all_teams.json()["items"][0]["competitionIds"] == [1]

    all_players = client.get("/api/v1/football/players")
    assert all_players.status_code == 200
    assert all_players.json()["items"][0]["name"] == "Lionel Messi"
    assert all_players.json()["items"][0]["competitionIds"] == [1]

    teams = client.get("/api/v1/football/teams/search?q=brazil")
    assert teams.status_code == 200
    assert [item["name"] for item in teams.json()["items"]] == ["Brazil"]

    players = client.get("/api/v1/football/players/search?q=messi")
    assert players.status_code == 200
    assert [item["name"] for item in players.json()["items"]] == ["Lionel Messi"]


def test_profile_validation():
    response = client.post(
        "/api/v1/users/profile",
        json={"name": "A", "username": "Bad username"},
    )
    assert response.status_code == 422

    incomplete_name = client.post(
        "/api/v1/users/profile",
        json={"name": "John D Doe", "username": "valid_user"},
    )
    assert incomplete_name.status_code == 422

    too_many_favorites = client.post(
        "/api/v1/users/profile",
        json={
            "name": "Valid Person",
            "username": "valid_person",
            "favoriteTeamIds": [str(index) for index in range(7)],
        },
    )
    assert too_many_favorites.status_code == 422


def test_username_availability():
    available = client.get("/api/v1/users/username/availability?username=fresh_user")
    assert available.status_code == 200
    assert available.json() == {"username": "fresh_user", "available": True}


def test_match_detail_normalization():
    response = client.get("/api/v1/matches/fifa.world/760422/detail")
    assert response.status_code == 200
    body = response.json()
    assert body["matchId"] == "760422"
    assert body["league"] == "fifa.world"
    assert body["status"] == "FT"
    assert body["statusDescription"] == "Full Time"
    assert body["homeTeam"]["name"] == "Germany"
    assert body["homeTeam"]["score"] == 7
    assert body["awayTeam"]["abbreviation"] == "CUW"
    assert body["venue"] == {"name": "Goalio Stadium", "city": "Berlin"}
    assert body["officials"][0] == {"name": "P. Sampaio", "role": "Referee"}
    assert body["weather"]["displayValue"] == "28°C Clear"
    assert body["teamStats"][0]["stats"][0]["value"] == "65%"
    assert body["playerLeaders"][0]["players"][0]["name"] == "Kai Havertz"
    assert body["playerLeaders"][0]["players"][0]["jersey"] == "7"
    assert body["playerLeaders"][0]["players"][0]["espnUrl"].endswith("kai-havertz")
    assert body["lineups"][0]["formation"] == "4-2-3-1"
    assert body["lineups"][0]["coach"] == "Julian Nagelsmann"
    assert body["lineups"][0]["starters"][0]["name"] == "Manuel Neuer"
    assert body["lineups"][0]["substitutes"][0]["jersey"] == "22"
    assert body["winProbability"]["homeWinPercentage"] == 100
    assert body["events"][0]["minute"] == "38'"
    assert body["summary"] == "Germany won comfortably."


def test_match_scoreboard_returns_event_ids_for_detail():
    response = client.get("/api/v1/matches/fifa.world/scoreboard")
    assert response.status_code == 200
    body = response.json()
    assert body["league"] == "fifa.world"
    assert body["matches"][0]["matchId"] == "760422"
    assert body["matches"][0]["name"] == "Germany vs Curacao"
    assert body["matches"][0]["shortName"] == "GER v CUW"
    assert body["matches"][0]["homeTeam"]["name"] == "Germany"
    assert body["matches"][0]["awayTeam"]["score"] == 1
    assert body["matches"][0]["statusDescription"] == "Full Time"
    assert body["matches"][0]["state"] == "post"
    assert body["matches"][0]["detailApi"] == "/api/matches/fifa.world/760422/detail"


def test_match_lineup_returns_normalized_pitch_response():
    response = client.get("/api/v1/matches/760422/lineup?league=fifa.world")
    assert response.status_code == 200
    body = response.json()
    assert body["eventId"] == "760422"
    assert body["status"] == "PARTIAL"
    assert body["source"] == "espn"
    assert body["home"]["formation"] == "4-2-3-1"
    assert body["home"]["manager"]["name"] == "Julian Nagelsmann"
    assert body["home"]["startingXI"][0]["name"] == "Manuel Neuer"
    assert body["home"]["startingXI"][0]["x"] == 50
    assert body["home"]["startingXI"][0]["y"] == 92
    assert body["home"]["bench"][0]["number"] == 22


def test_worldcup_bootstrap_returns_compact_library_payload():
    response = client.get("/api/v1/worldcup/bootstrap")
    assert response.status_code == 200
    body = response.json()
    assert body["tournament"]["id"] == "worldcup-2026"
    assert body["tournament"]["hostCities"] == 16
    assert body["groups"][0]["code"] == "A"
    assert body["groups"][0]["teams"][0]["name"] == "Germany"
    assert body["groups"][0]["teams"][0]["goalsFor"] == 5
    assert body["groups"][0]["teams"][0]["goalDifference"] == 4
    assert body["bracket"]["bracketType"] == "32_TEAM_KNOCKOUT"
    assert len(body["bracket"]["rounds"]["R32"]) == 16
    assert len(body["bracket"]["rounds"]["R16"]) == 8
    assert len(body["bracket"]["rounds"]["QF"]) == 4
    assert len(body["bracket"]["rounds"]["SF"]) == 2
    assert len(body["bracket"]["rounds"]["FINAL"]) == 1
    assert body["bracket"]["rounds"]["FINAL"][0]["slotIndex"] == 0
    assert body["library"][0]["id"] == "pele-legacy"
    assert body["randomFact"]["title"]


def test_worldcup_bracket_reclassifies_dirty_placeholder_matches_and_links_slots():
    real_r32 = ScoreboardMatch(
        matchId="r32-real",
        league="fifa.world",
        name="World Cup Match 73",
        status="FT",
        statusDescription="Round of 32",
        state="post",
        homeTeam=MatchTeam(id="1", name="Germany", shortName="Germany", score=2),
        awayTeam=MatchTeam(id="2", name="Paraguay", shortName="Paraguay", score=1),
        detailApi="/detail/r32-real",
    )
    dirty_r16 = ScoreboardMatch(
        matchId="future-r16",
        league="fifa.world",
        name="Mexico vs RD32 W8",
        status="TBD",
        statusDescription="Round of 32",
        state="pre",
        homeTeam=MatchTeam(id="3", name="Mexico", shortName="Mexico"),
        awayTeam=MatchTeam(id="placeholder", name="RD32 W8", shortName="RD32 W8"),
        detailApi="/detail/future-r16",
    )
    real_r32_slot2 = real_r32.model_copy(update={"matchId": "r32-real-slot-2", "name": "World Cup Match 74"})

    bracket = _bracket([real_r32, real_r32_slot2, dirty_r16])

    assert {round_code: len(items) for round_code, items in bracket.rounds.items()} == {
        "R32": 16, "R16": 8, "QF": 4, "SF": 2, "FINAL": 1
    }
    assert bracket.rounds["R32"][0].eventId == "r32-real"
    assert bracket.rounds["R32"][2].eventId == "r32-real-slot-2"
    normalized_future = next(item for item in bracket.rounds["R16"] if item.eventId == "future-r16")
    assert normalized_future.slotIndex == 3
    assert normalized_future.awayTeam == "Winner of R32 Match 8"
    assert bracket.rounds["R32"][0].nextMatchSlot.slotIndex == 0
    assert bracket.rounds["R32"][0].nextMatchSlot.teamPosition == "home"
    assert bracket.rounds["R16"][0].nextMatchSlot.teamPosition == "away"
    assert bracket.rounds["R16"][1].nextMatchSlot.teamPosition == "home"
    assert bracket.rounds["QF"][0].homeTeam == "Winner of R16 Match 2"
    assert bracket.rounds["QF"][0].awayTeam == "Winner of R16 Match 1"
    assert bracket.rounds["FINAL"][0].homeTeam == "Winner of SF Match 1"
    assert bracket.rounds["FINAL"][0].nextMatchSlot is None


def test_worldcup_bracket_endpoint_returns_normalized_tree_contract():
    response = client.get("/api/v1/worldcup/bracket")
    assert response.status_code == 200
    body = response.json()
    assert body["tournament"] == "FIFA World Cup"
    assert body["bracketType"] == "32_TEAM_KNOCKOUT"
    assert list(body["rounds"]) == ["R32", "R16", "QF", "SF", "FINAL"]
    assert [match["slotIndex"] for match in body["rounds"]["R32"]] == list(range(16))
    assert body["rounds"]["R32"][1]["nextMatchSlot"] == {
        "round": "R16", "slotIndex": 0, "teamPosition": "away"
    }


def test_worldcup_bracket_uses_google_sky_topology_for_live_espn_events():
    r32_fixtures = [
        ("760486", "South Africa", "Canada"),
        ("760488", "Netherlands", "Morocco"),
        ("760489", "Germany", "Paraguay"),
        ("760492", "France", "Sweden"),
        ("760496", "Portugal", "Croatia"),
        ("760497", "Spain", "Austria"),
        ("760494", "United States", "Bosnia-Herzegovina"),
        ("760493", "Belgium", "Senegal"),
        ("760487", "Brazil", "Japan"),
        ("760490", "Ivory Coast", "Norway"),
        ("760491", "Mexico", "Ecuador"),
        ("760495", "England", "Congo DR"),
        ("760500", "Argentina", "Cape Verde"),
        ("760499", "Australia", "Egypt"),
        ("760498", "Switzerland", "Algeria"),
        ("760501", "Colombia", "Ghana"),
    ]

    def event(event_id: str, home: str, away: str, name: str | None = None) -> ScoreboardMatch:
        return ScoreboardMatch(
            matchId=event_id,
            league="fifa.world",
            name=name or f"{away} at {home}",
            status="TBD",
            statusDescription="Scheduled",
            state="pre",
            homeTeam=MatchTeam(id=f"{event_id}-home", name=home, shortName=home),
            awayTeam=MatchTeam(id=f"{event_id}-away", name=away, shortName=away),
            detailApi=f"/detail/{event_id}",
        )

    raw = [event(event_id, home, away) for event_id, home, away in reversed(r32_fixtures)]
    raw += [
        event("760505", "Mexico", "Round of 32 8 Winner", "Round of 32 8 Winner at Mexico"),
        event("760510", "Round of 16 1 Winner", "Round of 16 2 Winner", "Round of 16 2 Winner at Round of 16 1 Winner"),
        event("760514", "Quarterfinal 1 Winner", "Quarterfinal 2 Winner", "Quarterfinal 2 Winner at Quarterfinal 1 Winner"),
        event("760517", "Semifinal 1 Winner", "Semifinal 2 Winner", "Semifinal 2 Winner at Semifinal 1 Winner"),
        event("760516", "Semifinal 1 Loser", "Semifinal 2 Loser", "Semifinal 2 Loser at Semifinal 1 Loser"),
    ]

    bracket = _bracket(raw)

    assert [(item.homeTeam, item.awayTeam) for item in bracket.rounds["R32"]] == [
        (home, away) for _, home, away in r32_fixtures
    ]
    assert bracket.rounds["R16"][5].eventId == "760505"
    assert bracket.rounds["R16"][5].awayTeam == "Winner of R32 Match 12"
    assert bracket.rounds["QF"][0].eventId == "760510"
    assert bracket.rounds["QF"][0].homeTeam == "Winner of R16 Match 2"
    assert bracket.rounds["QF"][0].awayTeam == "Winner of R16 Match 1"
    assert bracket.rounds["SF"][0].eventId == "760514"
    assert bracket.rounds["FINAL"][0].eventId == "760517"
    assert all(item.eventId != "760516" for items in bracket.rounds.values() for item in items)


def test_match_scoreboard_rejects_malformed_dates():
    response = client.get("/api/v1/matches/fifa.world/scoreboard?dates=2026069")
    assert response.status_code == 422
    assert response.json()["detail"] == "dates must be YYYYMMDD or YYYYMMDD-YYYYMMDD"


def test_match_schedule_accepts_iso_date_and_range():
    single = client.get("/api/v1/matches/fifa.world/schedule?date=2026-06-14")
    assert single.status_code == 200
    assert single.json()["date"] == "2026-06-14"
    assert single.json()["matches"][0]["matchId"] == "760422"

    date_range = client.get("/api/v1/matches/fifa.world/schedule?from=2026-06-01&to=2026-06-30")
    assert date_range.status_code == 200
    assert date_range.json()["date"] == "2026-06-01/2026-06-30"


def test_match_schedule_rejects_bad_iso_date():
    response = client.get("/api/v1/matches/fifa.world/schedule?date=20260614")
    assert response.status_code == 422
    assert response.json()["detail"] == "date must be YYYY-MM-DD"


def test_quiz_session_has_five_questions_without_answers_and_updates_xp():
    started = client.post("/api/v1/quiz/sessions")
    assert started.status_code == 200
    session = started.json()
    assert len(session["questions"]) == 5
    assert "correctAnswerIndex" not in session["questions"][0]
    question = session["questions"][0]
    correct_index = QUESTION_BY_ID[question["id"]][4]
    answered = client.post(f"/api/v1/quiz/sessions/{session['sessionId']}/answer", json={"questionId": question["id"], "answerIndex": correct_index})
    assert answered.status_code == 200
    assert answered.json()["correct"] is True
    assert answered.json()["xpDelta"] == 10


def test_quiz_wrong_answer_has_negative_xp_and_leaderboard_uses_username():
    session = client.post("/api/v1/quiz/sessions").json()
    question = session["questions"][0]
    correct = QUESTION_BY_ID[question["id"]][4]
    result = client.post(f"/api/v1/quiz/sessions/{session['sessionId']}/answer", json={"questionId": question["id"], "answerIndex": (correct + 1) % 4}).json()
    assert result["xpDelta"] == -5
    leaderboard = client.get("/api/v1/quiz/leaderboard").json()
    assert leaderboard["entries"][0]["username"] == "aegies"


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
