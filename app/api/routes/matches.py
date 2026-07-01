from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.dependencies import CurrentUser, get_current_user, get_lineup_store, get_match_detail_client, get_match_detail_store, get_scoreboard_store, get_thesportsdb_provider
from app.schemas.lineups import MatchLineupResponse
from app.schemas.matches import MatchDetail, ScoreboardResponse, StandingsResponse
from app.services.lineups import LineupService, LineupStore
from app.services.match_detail import EspnMatchDetailClient, MatchDetailStore, ScoreboardStore, validate_scoreboard_dates
from app.core.config import get_settings
from app.services.lineup_providers.thesportsdb import TheSportsDbProvider


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
    store: MatchDetailStore = Depends(get_match_detail_store),
) -> MatchDetail:
    return client.cached_detail(league, event_id, store)


@router.get("/{event_id}/lineup", response_model=MatchLineupResponse)
def match_lineup(
    event_id: str = Path(max_length=40),
    league: str = Query(default="fifa.world", max_length=40),
    _: CurrentUser = Depends(get_current_user),
    client: EspnMatchDetailClient = Depends(get_match_detail_client),
    store: LineupStore = Depends(get_lineup_store),
    thesportsdb: TheSportsDbProvider = Depends(get_thesportsdb_provider),
    force_refresh: bool = Query(default=False, alias="forceRefresh"),
) -> MatchLineupResponse:
    if force_refresh and not (get_settings().lineup_debug or get_settings().app_env == "development"):
        raise HTTPException(403, "forceRefresh is only available in debug/admin mode")
    return LineupService(client, store, thesportsdb).get(league, event_id, force=force_refresh)


debug_router = APIRouter(prefix="/debug/matches", tags=["debug"])


@debug_router.get("/{event_id}/lineup-provider")
def lineup_provider(
    event_id: str = Path(max_length=40),
    league: str = Query(default="fifa.world", max_length=40),
    _: CurrentUser = Depends(get_current_user),
    client: EspnMatchDetailClient = Depends(get_match_detail_client),
    store: LineupStore = Depends(get_lineup_store),
    thesportsdb: TheSportsDbProvider = Depends(get_thesportsdb_provider),
) -> dict:
    if not (get_settings().lineup_debug or get_settings().app_env == "development"):
        raise HTTPException(404, "Not found")
    service = LineupService(client, store, thesportsdb)
    service.get(league, event_id, force=True)
    return service.last_debug


@router.get("/{league}/scoreboard", response_model=ScoreboardResponse)
def match_scoreboard(
    league: str = Path(max_length=40),
    dates: str | None = Query(default=None, max_length=17),
    _: CurrentUser = Depends(get_current_user),
    client: EspnMatchDetailClient = Depends(get_match_detail_client),
) -> ScoreboardResponse:
    validate_scoreboard_dates(dates)
    return client.scoreboard(league, dates)


@router.get("/{league}/standings", response_model=StandingsResponse)
def match_standings(
    league: str = Path(max_length=40),
    season: int | None = Query(default=None, ge=2000, le=2100),
    _: CurrentUser = Depends(get_current_user),
    client: EspnMatchDetailClient = Depends(get_match_detail_client),
) -> StandingsResponse:
    return client.standings(league, season)


@router.get("/{league}/schedule", response_model=ScoreboardResponse)
def match_schedule(
    league: str = Path(max_length=40),
    date: str | None = Query(default=None, max_length=10),
    from_date: str | None = Query(default=None, alias="from", max_length=10),
    to_date: str | None = Query(default=None, alias="to", max_length=10),
    _: CurrentUser = Depends(get_current_user),
    client: EspnMatchDetailClient = Depends(get_match_detail_client),
    store: ScoreboardStore = Depends(get_scoreboard_store),
) -> ScoreboardResponse:
    return client.cached_schedule(league, store, date=date, from_date=from_date, to_date=to_date)
