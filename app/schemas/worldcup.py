from pydantic import BaseModel, Field

from app.schemas.matches import ScoreboardMatch, StandingTeam


class WorldCupTournament(BaseModel):
    id: str
    name: str
    stage: str
    hostCities: int
    daysToFinal: int | None = None
    lastSyncedAt: str | None = None


class WorldCupGroup(BaseModel):
    code: str
    teams: list[StandingTeam] = Field(default_factory=list)


class WorldCupBracketMatch(BaseModel):
    eventId: str
    round: str
    matchNumber: int | None = None
    status: str | None = None
    homeTeam: str | None = None
    awayTeam: str | None = None
    homeTeamLogo: str | None = None
    awayTeamLogo: str | None = None
    homeScore: int | None = None
    awayScore: int | None = None
    winnerTeamId: str | None = None
    kickoff: str | None = None


class WorldCupBracketRound(BaseModel):
    round: str
    matches: list[WorldCupBracketMatch] = Field(default_factory=list)


class WorldCupLibraryItem(BaseModel):
    id: str
    title: str
    category: str
    body: str
    readMinutes: int


class WorldCupFact(BaseModel):
    title: str
    body: str


class WorldCupBootstrap(BaseModel):
    tournament: WorldCupTournament
    liveMatches: list[ScoreboardMatch] = Field(default_factory=list)
    todayMatches: list[ScoreboardMatch] = Field(default_factory=list)
    upcomingMatches: list[ScoreboardMatch] = Field(default_factory=list)
    recentResults: list[ScoreboardMatch] = Field(default_factory=list)
    groups: list[WorldCupGroup] = Field(default_factory=list)
    bracket: list[WorldCupBracketRound] = Field(default_factory=list)
    library: list[WorldCupLibraryItem] = Field(default_factory=list)
    randomFact: WorldCupFact
