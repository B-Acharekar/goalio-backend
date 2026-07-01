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


class MatchOfficial(BaseModel):
    name: str | None = None
    role: str | None = None


class MatchWeather(BaseModel):
    displayValue: str | None = None
    temperature: str | None = None
    condition: str | None = None


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


class LineupPlayer(BaseModel):
    id: str | None = None
    name: str
    position: str | None = None
    jersey: str | None = None
    starter: bool = False
    captain: bool = False
    substitute: bool = False
    formationPlace: str | None = None


class TeamLineup(BaseModel):
    teamId: str | None = None
    teamName: str | None = None
    formation: str | None = None
    coach: str | None = None
    starters: list[LineupPlayer] = Field(default_factory=list)
    substitutes: list[LineupPlayer] = Field(default_factory=list)


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
    officials: list[MatchOfficial] = Field(default_factory=list)
    weather: MatchWeather | None = None
    teamStats: list[TeamStats] = Field(default_factory=list)
    playerLeaders: list[PlayerLeaderCategory] = Field(default_factory=list)
    lineups: list[TeamLineup] = Field(default_factory=list)
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


class StandingTeam(BaseModel):
    rank: int | None = None
    teamId: str
    name: str
    abbreviation: str | None = None
    logo: str | None = None
    group: str | None = None
    stage: str | None = None
    played: int | None = None
    wins: int | None = None
    draws: int | None = None
    losses: int | None = None
    points: int | None = None


class StandingsResponse(BaseModel):
    league: str
    season: int | None = None
    groups: list[str] = Field(default_factory=list)
    teams: list[StandingTeam] = Field(default_factory=list)
