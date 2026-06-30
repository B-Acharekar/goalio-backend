from fastapi import APIRouter, Depends, Path, Query

from app.api.dependencies import CurrentUser, get_current_user, get_match_detail_client
from app.schemas.matches import MatchDetail, ScoreboardResponse
from app.services.match_detail import EspnMatchDetailClient


router = APIRouter(
    prefix="/matches",
    tags=["matches"],
    responses={
        401: {"description": "Missing, invalid, expired, or revoked Firebase ID token"},
        422: {"description": "Unsupported league or invalid request"},
        502: {"description": "ESPN match summary is temporarily unavailable"},
    },
)


@router.get("/{league}/{event_id}/detail", response_model=MatchDetail)
def match_detail(
    league: str = Path(max_length=40),
    event_id: str = Path(max_length=40),
    _: CurrentUser = Depends(get_current_user),
    client: EspnMatchDetailClient = Depends(get_match_detail_client),
) -> MatchDetail:
    return client.detail(league, event_id)


@router.get("/{league}/scoreboard", response_model=ScoreboardResponse)
def match_scoreboard(
    league: str = Path(max_length=40),
    dates: str | None = Query(default=None, max_length=16),
    _: CurrentUser = Depends(get_current_user),
    client: EspnMatchDetailClient = Depends(get_match_detail_client),
) -> ScoreboardResponse:
    return client.scoreboard(league, dates)
