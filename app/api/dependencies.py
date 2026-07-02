from dataclasses import dataclass
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth

from app.core.config import get_settings
from app.core.firebase import get_firestore_client, initialize_firebase
from app.repositories.football import FirestoreFootballRepository, FootballRepository
from app.repositories.profiles import FirestoreProfileRepository, ProfileRepository
from app.services.match_detail import EspnMatchDetailClient, FirestoreMatchDetailStore, FirestoreScoreboardStore, MatchDetailStore, ScoreboardStore
from app.services.lineups import FirestoreLineupStore, LineupStore
from app.services.lineup_providers.thesportsdb import FirestoreProviderMappingStore, TheSportsDbProvider
from app.services.quiz import FirestoreQuizRepository, QuizRepository


bearer = HTTPBearer(
    auto_error=False,
    description=(
        "Firebase ID token from the Android app. For local Swagger testing only, "
        "set ALLOW_DEV_AUTH=true and use dev:swagger-user as the token."
    ),
)


@dataclass(frozen=True)
class CurrentUser:
    uid: str


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser:
    settings = get_settings()
    dev_auth_enabled = settings.allow_dev_auth and settings.app_env == "development"
    if dev_auth_enabled and credentials and credentials.credentials.startswith("dev:"):
        return CurrentUser(credentials.credentials.removeprefix("dev:"))
    if credentials is None:
        if dev_auth_enabled:
            return CurrentUser("swagger-user")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Firebase ID token")
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Firebase ID token")
    try:
        initialize_firebase()
        decoded = auth.verify_id_token(credentials.credentials, check_revoked=True)
        return CurrentUser(decoded["uid"])
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired Firebase ID token") from exc


@lru_cache
def get_profile_repository() -> ProfileRepository:
    return FirestoreProfileRepository(get_firestore_client())


@lru_cache
def get_football_repository() -> FootballRepository:
    return FirestoreFootballRepository(get_firestore_client())


@lru_cache
def get_match_detail_client() -> EspnMatchDetailClient:
    return EspnMatchDetailClient(thesportsdb=get_thesportsdb_provider)


@lru_cache
def get_match_detail_store() -> MatchDetailStore:
    return FirestoreMatchDetailStore(get_firestore_client())


@lru_cache
def get_scoreboard_store() -> ScoreboardStore:
    return FirestoreScoreboardStore(get_firestore_client())


@lru_cache
def get_lineup_store() -> LineupStore:
    return FirestoreLineupStore(get_firestore_client())


@lru_cache
def get_thesportsdb_provider() -> TheSportsDbProvider:
    settings = get_settings()
    return TheSportsDbProvider(settings.thesportsdb_api_key, settings.thesportsdb_base_url,
                               settings.thesportsdb_use_v2_fallback, FirestoreProviderMappingStore(get_firestore_client))


@lru_cache
def get_quiz_repository() -> QuizRepository:
    return FirestoreQuizRepository(get_firestore_client())
