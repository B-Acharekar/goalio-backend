from fastapi import APIRouter, Depends

from app.api.dependencies import CurrentUser, get_current_user, get_match_detail_client
from app.schemas.worldcup import WorldCupBootstrap, WorldCupBracketRound, WorldCupGroup, WorldCupLibraryItem
from app.services.match_detail import EspnMatchDetailClient
from app.services.worldcup import WorldCupService


router = APIRouter(
    prefix="/worldcup",
    tags=["worldcup"],
    responses={401: {"description": "Missing, invalid, expired, or revoked Firebase ID token"}},
)


def get_worldcup_service(
    client: EspnMatchDetailClient = Depends(get_match_detail_client),
) -> WorldCupService:
    return WorldCupService(client)


@router.get("/bootstrap", response_model=WorldCupBootstrap)
def worldcup_bootstrap(
    _: CurrentUser = Depends(get_current_user),
    service: WorldCupService = Depends(get_worldcup_service),
) -> WorldCupBootstrap:
    return service.bootstrap()


@router.get("/groups", response_model=list[WorldCupGroup])
def worldcup_groups(
    _: CurrentUser = Depends(get_current_user),
    service: WorldCupService = Depends(get_worldcup_service),
) -> list[WorldCupGroup]:
    return service.groups()


@router.get("/bracket", response_model=list[WorldCupBracketRound])
def worldcup_bracket(
    _: CurrentUser = Depends(get_current_user),
    service: WorldCupService = Depends(get_worldcup_service),
) -> list[WorldCupBracketRound]:
    return service.bracket()


@router.get("/library", response_model=list[WorldCupLibraryItem])
def worldcup_library(
    _: CurrentUser = Depends(get_current_user),
    service: WorldCupService = Depends(get_worldcup_service),
) -> list[WorldCupLibraryItem]:
    return service.library()
