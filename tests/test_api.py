from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.dependencies import (
    CurrentUser,
    get_current_user,
    get_football_repository,
    get_match_detail_client,
    get_profile_repository,
)
from app.main import app
from app.schemas.football import PlayerPage, PlayerResult, TeamPage, TeamResult
from app.schemas.profile import ProfileUpsert, UserProfile


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


class MemoryFootball:
    def list_teams(self, limit: int, cursor: str | None) -> TeamPage:
        return TeamPage(items=[TeamResult(id="6", name="Brazil", shortName="BRA", competitionIds=[1])])

    def list_players(self, limit: int, cursor: str | None) -> PlayerPage:
        return PlayerPage(items=[PlayerResult(id="154", name="Lionel Messi", team="Argentina", competitionIds=[1])])

    def search_teams(self, query: str, limit: int, cursor: str | None) -> TeamPage:
        teams = [TeamResult(id="6", name="Brazil", shortName="BRA", competitionIds=[1])]
        return TeamPage(items=[team for team in teams if query.casefold() in team.name.casefold()][:limit])

    def search_players(self, query: str, limit: int, cursor: str | None) -> PlayerPage:
        players = [PlayerResult(id="154", name="Lionel Messi", team="Argentina", competitionIds=[1])]
        return PlayerPage(items=[player for player in players if query.casefold() in player.name.casefold()][:limit])


class MemoryMatchDetail:
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


repository = MemoryProfiles()
app.dependency_overrides[get_current_user] = lambda: CurrentUser("test-user")
app.dependency_overrides[get_profile_repository] = lambda: repository
app.dependency_overrides[get_football_repository] = lambda: MemoryFootball()
app.dependency_overrides[get_match_detail_client] = lambda: MemoryMatchDetail()
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


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
