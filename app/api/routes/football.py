from fastapi import APIRouter, Depends, Query

from app.api.dependencies import CurrentUser, get_current_user, get_football_repository
from app.repositories.football import FootballRepository
from app.schemas.football import PlayerPage, PlayerResult, TeamPage, TeamResult


router = APIRouter(
    prefix="/football",
    tags=["football"],
    responses={401: {"description": "Missing, invalid, expired, or revoked Firebase ID token"}},
)


@router.get("/teams", response_model=TeamPage)
def teams_list(
    limit: int = Query(default=6, ge=1, le=20),
    cursor: str | None = Query(default=None, max_length=40),
    competition_id: int | None = Query(default=None, alias="competitionId", ge=1),
    _: CurrentUser = Depends(get_current_user),
    repository: FootballRepository = Depends(get_football_repository),
) -> TeamPage:
    return repository.list_teams(limit, cursor, competition_id)


@router.get("/players", response_model=PlayerPage)
def players_list(
    limit: int = Query(default=6, ge=1, le=20),
    cursor: str | None = Query(default=None, max_length=40),
    competition_id: int | None = Query(default=None, alias="competitionId", ge=1),
    _: CurrentUser = Depends(get_current_user),
    repository: FootballRepository = Depends(get_football_repository),
) -> PlayerPage:
    return repository.list_players(limit, cursor, competition_id)


@router.get("/teams/search", response_model=TeamPage)
def teams_search(
    q: str = Query(default="", max_length=80),
    limit: int = Query(default=6, ge=1, le=20),
    cursor: str | None = Query(default=None, max_length=40),
    _: CurrentUser = Depends(get_current_user),
    repository: FootballRepository = Depends(get_football_repository),
) -> TeamPage:
    return repository.search_teams(q, limit, cursor)


@router.get("/players/search", response_model=PlayerPage)
def players_search(
    q: str = Query(default="", max_length=80),
    limit: int = Query(default=6, ge=1, le=20),
    cursor: str | None = Query(default=None, max_length=40),
    _: CurrentUser = Depends(get_current_user),
    repository: FootballRepository = Depends(get_football_repository),
) -> PlayerPage:
    return repository.search_players(q, limit, cursor)
