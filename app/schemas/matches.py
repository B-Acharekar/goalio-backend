from pydantic import BaseModel, Field


class MatchTeam(BaseModel):
    id: str
    name: str
    shortName: str | None = None
    abbreviation: str | None = None
    logo: str | None = None
    score: int | None = None


class MatchVenue(BaseModel):
    name: str | None = None
    city: str | None = None


class MatchStat(BaseModel):
    name: str
    label: str
    value: str


class TeamStats(BaseModel):
    teamId: str
    stats: list[MatchStat] = Field(default_factory=list)


class MatchLeaderPlayer(BaseModel):
    id: str
    name: str
    position: str | None = None
    jersey: str | None = None
    espnUrl: str | None = None
    mainStat: str | None = None
    stats: list[MatchStat] = Field(default_factory=list)


class PlayerLeaderCategory(BaseModel):
    category: str
    players: list[MatchLeaderPlayer] = Field(default_factory=list)


class MatchEvent(BaseModel):
    minute: str | None = None
    type: str | None = None
    text: str
    team: str | None = None


class WinProbability(BaseModel):
    homeWinPercentage: int
    awayWinPercentage: int
    drawPercentage: int | None = None


class MatchDetail(BaseModel):
    matchId: str
    league: str
    status: str | None = None
    statusDescription: str | None = None
    kickoff: str | None = None
    homeTeam: MatchTeam | None = None
    awayTeam: MatchTeam | None = None
    venue: MatchVenue | None = None
    teamStats: list[TeamStats] = Field(default_factory=list)
    playerLeaders: list[PlayerLeaderCategory] = Field(default_factory=list)
    events: list[MatchEvent] = Field(default_factory=list)
    summary: str | None = None
    winProbability: WinProbability | None = None


class ScoreboardMatch(BaseModel):
    matchId: str
    league: str
    name: str | None = None
    shortName: str | None = None
    status: str | None = None
    statusDescription: str | None = None
    state: str | None = None
    kickoff: str | None = None
    homeTeam: MatchTeam | None = None
    awayTeam: MatchTeam | None = None
    venue: MatchVenue | None = None
    detailApi: str


class ScoreboardResponse(BaseModel):
    league: str
    date: str | None = None
    matches: list[ScoreboardMatch] = Field(default_factory=list)
