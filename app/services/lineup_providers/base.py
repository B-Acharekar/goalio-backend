from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.schemas.matches import MatchDetail, TeamLineup


@dataclass(frozen=True)
class MatchMeta:
    event_id: str
    league: str
    home_team: str
    away_team: str
    home_team_id: str | None
    away_team_id: str | None
    home_logo: str | None
    away_logo: str | None
    kickoff: datetime | None
    status: str | None

    @classmethod
    def from_espn(cls, detail: MatchDetail) -> "MatchMeta":
        return cls(
            event_id=detail.matchId,
            league=detail.league,
            home_team=(detail.homeTeam.shortName or detail.homeTeam.name) if detail.homeTeam else "Home",
            away_team=(detail.awayTeam.shortName or detail.awayTeam.name) if detail.awayTeam else "Away",
            home_team_id=detail.homeTeam.id if detail.homeTeam else None,
            away_team_id=detail.awayTeam.id if detail.awayTeam else None,
            home_logo=detail.homeTeam.logo if detail.homeTeam else None,
            away_logo=detail.awayTeam.logo if detail.awayTeam else None,
            kickoff=_parse_datetime(detail.kickoff),
            status=detail.statusDescription or detail.status,
        )


@dataclass
class ProviderResult:
    lineups: list[TeamLineup] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
