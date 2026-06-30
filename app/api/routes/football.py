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
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=40),
    _: CurrentUser = Depends(get_current_user),
    repository: FootballRepository = Depends(get_football_repository),
) -> TeamPage:
    return repository.list_teams(limit, cursor)


@router.get("/players", response_model=PlayerPage)
def players_list(
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=40),
    _: CurrentUser = Depends(get_current_user),
    repository: FootballRepository = Depends(get_football_repository),
) -> PlayerPage:
    return repository.list_players(limit, cursor)


@router.get("/teams/search", response_model=list[TeamResult])
def teams_search(
    q: str = Query(default="", max_length=80),
    _: CurrentUser = Depends(get_current_user),
    repository: FootballRepository = Depends(get_football_repository),
) -> list[TeamResult]:
    return repository.search_teams(q)


@router.get("/players/search", response_model=list[PlayerResult])
def players_search(
    q: str = Query(default="", max_length=80),
    _: CurrentUser = Depends(get_current_user),
    repository: FootballRepository = Depends(get_football_repository),
) -> list[PlayerResult]:
    return repository.search_players(q)
