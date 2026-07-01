from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


LineupStatus = Literal["NOT_AVAILABLE", "PARTIAL", "PROBABLE", "CONFIRMED", "LIVE", "FINAL"]
LineupSource = Literal["espn", "theSportsDb", "cache", "generated"]
FormationStatus = Literal["CONFIRMED", "ESTIMATED", "UNKNOWN"]


class LineupManager(BaseModel):
    name: str
    photo: str | None = None


class NormalizedLineupPlayer(BaseModel):
    id: str | None = None
    name: str
    number: int | None = None
    position: str | None = None
    role: str | None = None
    photo: str | None = None
    captain: bool = False
    x: float | None = Field(default=None, ge=0, le=100)
    y: float | None = Field(default=None, ge=0, le=100)


class UnavailablePlayer(BaseModel):
    name: str
    reason: str


class NormalizedTeamLineup(BaseModel):
    teamId: str | None = None
    teamName: str | None = None
    teamLogo: str | None = None
    formation: str | None = None
    manager: LineupManager | None = None
    startingXI: list[NormalizedLineupPlayer] = Field(default_factory=list)
    bench: list[NormalizedLineupPlayer] = Field(default_factory=list)
    unavailable: list[UnavailablePlayer] = Field(default_factory=list)


class MatchLineupResponse(BaseModel):
    eventId: str
    status: LineupStatus
    source: LineupSource
    formationStatus: FormationStatus
    lastUpdated: datetime
    nextRefreshAt: datetime | None = None
    kickoff: datetime | None = None
    isStale: bool = False
    home: NormalizedTeamLineup
    away: NormalizedTeamLineup
