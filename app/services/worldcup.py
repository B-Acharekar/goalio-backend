from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.schemas.matches import ScoreboardMatch, StandingTeam
from app.schemas.worldcup import (
    WorldCupBootstrap,
    WorldCupBracketMatch,
    WorldCupBracketRound,
    WorldCupFact,
    WorldCupGroup,
    WorldCupLibraryItem,
    WorldCupTournament,
)
from app.services.match_detail import EspnMatchDetailClient


WORLD_CUP_LEAGUE = "fifa.world"
WORLD_CUP_FINAL = date(2026, 7, 19)


LIBRARY_ITEMS = [
    WorldCupLibraryItem(
        id="pele-legacy",
        title="The Legacy of Pele",
        category="History",
        body="The only player to win three FIFA World Cups became the tournament's enduring icon.",
        readMinutes=12,
    ),
    WorldCupLibraryItem(
        id="host-cities-2026",
        title="2026 Host Cities",
        category="Guide",
        body="The 2026 edition is staged across 16 cities in Canada, Mexico, and the United States.",
        readMinutes=6,
    ),
    WorldCupLibraryItem(
        id="knockout-format-2026",
        title="Round of 32 Explained",
        category="Format",
        body="The expanded 48-team format sends 32 teams into a single-elimination knockout path.",
        readMinutes=4,
    ),
]

FACTS = [
    WorldCupFact(title="Most titles", body="Brazil have won the men's FIFA World Cup five times."),
    WorldCupFact(title="2026 hosts", body="Canada, Mexico, and the United States co-host the 2026 tournament."),
    WorldCupFact(title="Expanded field", body="The 2026 World Cup is the first men's edition planned for 48 teams."),
]


class WorldCupService:
    def __init__(self, match_client: EspnMatchDetailClient):
        self.match_client = match_client

    def bootstrap(self, today: date | None = None) -> WorldCupBootstrap:
        current_date = today or date.today()
        schedule = self.match_client.schedule(
            WORLD_CUP_LEAGUE,
            date=None,
            from_date=(current_date - timedelta(days=7)).isoformat(),
            to_date=(current_date + timedelta(days=21)).isoformat(),
        )
        matches = schedule.matches
        live = [match for match in matches if match.state == "in"][:3]
        today_matches = [match for match in matches if _match_date(match) == current_date][:6]
        upcoming = [match for match in matches if match.state == "pre"][:6]
        recent = [match for match in matches if match.state == "post"][:6]
        standings = self.match_client.standings(WORLD_CUP_LEAGUE)
        groups = _groups(standings.teams)
        return WorldCupBootstrap(
            tournament=WorldCupTournament(
                id="worldcup-2026",
                name="FIFA World Cup 2026",
                stage=_stage_for_date(current_date),
                hostCities=16,
                daysToFinal=max(0, (WORLD_CUP_FINAL - current_date).days),
                lastSyncedAt=datetime.now(timezone.utc).isoformat(),
            ),
            liveMatches=live,
            todayMatches=today_matches,
            upcomingMatches=upcoming,
            recentResults=recent,
            groups=groups[:12],
            bracket=_bracket(matches),
            library=LIBRARY_ITEMS,
            randomFact=FACTS[current_date.toordinal() % len(FACTS)],
        )

    def groups(self) -> list[WorldCupGroup]:
        return _groups(self.match_client.standings(WORLD_CUP_LEAGUE).teams)

    def bracket(self) -> list[WorldCupBracketRound]:
        current_date = date.today()
        schedule = self.match_client.schedule(
            WORLD_CUP_LEAGUE,
            date=None,
            from_date=(current_date - timedelta(days=7)).isoformat(),
            to_date=(current_date + timedelta(days=21)).isoformat(),
        )
        return _bracket(schedule.matches)

    def library(self) -> list[WorldCupLibraryItem]:
        return LIBRARY_ITEMS


def _groups(teams: list[StandingTeam]) -> list[WorldCupGroup]:
    buckets: dict[str, list[StandingTeam]] = {}
    for team in teams:
        code = (team.group or "Table").replace("Group ", "").strip() or "Table"
        buckets.setdefault(code, []).append(team)
    return [
        WorldCupGroup(code=code, teams=sorted(items, key=lambda item: item.rank or 999))
        for code, items in sorted(buckets.items())
    ]


def _bracket(matches: list[ScoreboardMatch]) -> list[WorldCupBracketRound]:
    rounds: dict[str, list[WorldCupBracketMatch]] = {}
    for match in matches:
        round_name = _round_name(match.statusDescription, match.name, match.kickoff)
        if round_name is None:
            continue
        rounds.setdefault(round_name, []).append(
            WorldCupBracketMatch(
                eventId=match.matchId,
                round=round_name,
                status=match.statusDescription or match.status,
                homeTeam=match.homeTeam.shortName if match.homeTeam else None,
                awayTeam=match.awayTeam.shortName if match.awayTeam else None,
                homeScore=match.homeTeam.score if match.homeTeam else None,
                awayScore=match.awayTeam.score if match.awayTeam else None,
                winnerTeamId=_winner(match),
                kickoff=match.kickoff,
            )
        )
    order = ["Round of 32", "Round of 16", "Quarterfinals", "Semifinals", "Final"]
    return [WorldCupBracketRound(round=name, matches=rounds.get(name, [])) for name in order if rounds.get(name)]


def _round_name(*values: str | None) -> str | None:
    joined = " ".join(value or "" for value in values).casefold()
    if "round of 32" in joined or "r32" in joined:
        return "Round of 32"
    if "round of 16" in joined or "r16" in joined:
        return "Round of 16"
    if "quarter" in joined:
        return "Quarterfinals"
    if "semi" in joined:
        return "Semifinals"
    if "final" in joined:
        return "Final"
    return None


def _winner(match: ScoreboardMatch) -> str | None:
    home_score = match.homeTeam.score if match.homeTeam else None
    away_score = match.awayTeam.score if match.awayTeam else None
    if match.state != "post" or home_score is None or away_score is None or home_score == away_score:
        return None
    return match.homeTeam.id if home_score > away_score and match.homeTeam else match.awayTeam.id if match.awayTeam else None


def _match_date(match: ScoreboardMatch) -> date | None:
    if not match.kickoff:
        return None
    try:
        return datetime.fromisoformat(match.kickoff.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _stage_for_date(value: date) -> str:
    if value <= date(2026, 6, 27):
        return "Group Stage"
    if value <= date(2026, 7, 3):
        return "Round of 32"
    if value <= date(2026, 7, 7):
        return "Round of 16"
    if value <= date(2026, 7, 11):
        return "Quarterfinals"
    if value <= date(2026, 7, 15):
        return "Semifinals"
    if value <= WORLD_CUP_FINAL:
        return "Final Week"
    return "Completed"
