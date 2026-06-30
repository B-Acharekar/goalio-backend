from fastapi import APIRouter, Depends, Query

from app.api.dependencies import CurrentUser, get_current_user, get_football_repository
from app.repositories.football import FootballRepository
from app.schemas.football import PlayerResult, TeamResult


router = APIRouter(
    prefix="/football",
    tags=["football"],
    responses={401: {"description": "Missing, invalid, expired, or revoked Firebase ID token"}},
)


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
