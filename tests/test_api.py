from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.dependencies import (
    CurrentUser,
    get_current_user,
    get_football_repository,
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
        saved = UserProfile(
            userId=uid,
            **profile.model_dump(),
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
        return TeamPage(items=[TeamResult(id="6", name="Brazil", shortName="BRA")])

    def list_players(self, limit: int, cursor: str | None) -> PlayerPage:
        return PlayerPage(items=[PlayerResult(id="154", name="Lionel Messi", team="Argentina")])

    def search_teams(self, query: str) -> list[TeamResult]:
        teams = [TeamResult(id="6", name="Brazil", shortName="BRA")]
        return [team for team in teams if query.casefold() in team.name.casefold()]

    def search_players(self, query: str) -> list[PlayerResult]:
        players = [PlayerResult(id="154", name="Lionel Messi", team="Argentina")]
        return [player for player in players if query.casefold() in player.name.casefold()]


repository = MemoryProfiles()
app.dependency_overrides[get_current_user] = lambda: CurrentUser("test-user")
app.dependency_overrides[get_profile_repository] = lambda: repository
app.dependency_overrides[get_football_repository] = lambda: MemoryFootball()
client = TestClient(app)


def test_profile_round_trip_and_personalized_home():
    payload = {
        "name": "Aegies User",
        "username": "aegies",
        "favoriteTeams": ["Brazil", "Argentina"],
        "favoritePlayers": ["Lionel Messi", "Neymar"],
        "onboardingCompleted": True,
    }
    created = client.post("/api/v1/users/profile", json=payload)
    assert created.status_code == 200
    assert created.json()["userId"] == "test-user"
    assert created.json()["profileCompleted"] is True

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

    all_players = client.get("/api/v1/football/players")
    assert all_players.status_code == 200
    assert all_players.json()["items"][0]["name"] == "Lionel Messi"

    teams = client.get("/api/v1/football/teams/search?q=brazil")
    assert teams.status_code == 200
    assert [item["name"] for item in teams.json()] == ["Brazil"]

    players = client.get("/api/v1/football/players/search?q=messi")
    assert players.status_code == 200
    assert [item["name"] for item in players.json()] == ["Lionel Messi"]


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


def test_username_availability():
    available = client.get("/api/v1/users/username/availability?username=fresh_user")
    assert available.status_code == 200
    assert available.json() == {"username": "fresh_user", "available": True}


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
