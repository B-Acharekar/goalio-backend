from typing import Any

import httpx
from fastapi import HTTPException, status

from app.schemas.matches import (
    MatchDetail,
    MatchEvent,
    MatchLeaderPlayer,
    MatchStat,
    MatchTeam,
    MatchVenue,
    PlayerLeaderCategory,
    ScoreboardMatch,
    ScoreboardResponse,
    TeamStats,
)


ESPN_SUMMARY_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
SUPPORTED_ESPN_LEAGUES = {
    "fifa.world",
    "eng.1",
    "esp.1",
    "ita.1",
    "ger.1",
    "fra.1",
    "usa.1",
    "uefa.champions",
    "uefa.europa",
}


class EspnMatchDetailClient:
    def __init__(self, base_url: str = ESPN_SUMMARY_BASE_URL, timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def detail(self, league: str, event_id: str) -> MatchDetail:
        _validate_league(league)
        try:
            response = httpx.get(
                f"{self.base_url}/{league}/summary",
                params={"event": event_id},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Match summary not found") from exc
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "ESPN match summary is temporarily unavailable",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "ESPN match summary is temporarily unavailable",
            ) from exc
        return normalize_espn_summary(league, event_id, response.json())

    def scoreboard(self, league: str, dates: str | None = None) -> ScoreboardResponse:
        _validate_league(league)
        validate_scoreboard_dates(dates)
        params = {"dates": dates} if dates else None
        try:
            response = httpx.get(
                f"{self.base_url}/{league}/scoreboard",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "League scoreboard not found") from exc
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "ESPN scoreboard is temporarily unavailable",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "ESPN scoreboard is temporarily unavailable",
            ) from exc
        return normalize_espn_scoreboard(league, response.json())


def normalize_espn_scoreboard(league: str, payload: dict[str, Any]) -> ScoreboardResponse:
    events = payload.get("events")
    matches: list[ScoreboardMatch] = []
    for event in events if isinstance(events, list) else []:
        event_dict = _as_dict(event)
        competition = _scoreboard_competition(event_dict)
        competitors = competition.get("competitors") or []
        home = _find_competitor(competitors, "home") or (competitors[0] if competitors else None)
        away = _find_competitor(competitors, "away") or (competitors[1] if len(competitors) > 1 else None)
        status_type = _as_dict(_as_dict(competition.get("status") or event_dict.get("status")).get("type"))
        match_id = _string(event_dict.get("id")) or _string(competition.get("id"))
        if not match_id:
            continue
        matches.append(
            ScoreboardMatch(
                matchId=match_id,
                league=league,
                status=_string(status_type.get("abbreviation")) or _string(status_type.get("shortDetail")),
                statusDescription=_string(status_type.get("detail"))
                or _string(status_type.get("description")),
                kickoff=_string(competition.get("date")) or _string(event_dict.get("date")),
                homeTeam=_team(home),
                awayTeam=_team(away),
                venue=_venue(competition.get("venue")),
            )
        )
    return ScoreboardResponse(league=league, matches=matches)


def normalize_espn_summary(league: str, event_id: str, payload: dict[str, Any]) -> MatchDetail:
    competition = _competition(payload)
    competitors = competition.get("competitors") or []
    home = _find_competitor(competitors, "home") or (competitors[0] if competitors else None)
    away = _find_competitor(competitors, "away") or (competitors[1] if len(competitors) > 1 else None)
    status_type = _as_dict(_as_dict(competition.get("status")).get("type"))
    article = _as_dict(payload.get("article"))

    return MatchDetail(
        matchId=str(event_id),
        league=league,
        status=_string(status_type.get("abbreviation")) or _string(status_type.get("shortDetail")),
        statusDescription=_string(status_type.get("detail"))
        or _string(status_type.get("description")),
        kickoff=_string(competition.get("date")) or _string(_as_dict(payload.get("header")).get("date")),
        homeTeam=_team(home),
        awayTeam=_team(away),
        venue=_venue(competition.get("venue")),
        teamStats=_team_stats(payload.get("boxscore")),
        playerLeaders=_player_leaders(payload.get("leaders")),
        events=_events(competition.get("details"), payload.get("commentary")),
        summary=_string(article.get("story")) or _string(article.get("description")),
    )


def _validate_league(league: str) -> None:
    if league not in SUPPORTED_ESPN_LEAGUES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unsupported ESPN soccer league: {league}",
        )


def validate_scoreboard_dates(dates: str | None) -> None:
    if dates is None:
        return
    parts = dates.split("-")
    valid = len(parts) in {1, 2} and all(len(part) == 8 and part.isdigit() for part in parts)
    if not valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "dates must be YYYYMMDD or YYYYMMDD-YYYYMMDD",
        )


def _competition(payload: dict[str, Any]) -> dict[str, Any]:
    header = _as_dict(payload.get("header"))
    competitions = header.get("competitions")
    if isinstance(competitions, list) and competitions:
        return _as_dict(competitions[0])
    return {}


def _scoreboard_competition(event: dict[str, Any]) -> dict[str, Any]:
    competitions = event.get("competitions")
    if isinstance(competitions, list) and competitions:
        return _as_dict(competitions[0])
    return {}


def _find_competitor(competitors: list[Any], home_away: str) -> dict[str, Any] | None:
    return next(
        (
            _as_dict(competitor)
            for competitor in competitors
            if _as_dict(competitor).get("homeAway") == home_away
        ),
        None,
    )


def _team(competitor: dict[str, Any] | None) -> MatchTeam | None:
    if not competitor:
        return None
    team = _as_dict(competitor.get("team"))
    team_id = _string(team.get("id")) or _string(competitor.get("id"))
    if not team_id:
        return None
    return MatchTeam(
        id=team_id,
        name=_string(team.get("displayName")) or _string(team.get("name")) or team_id,
        shortName=_string(team.get("shortDisplayName")) or _string(team.get("shortName")),
        abbreviation=_string(team.get("abbreviation")),
        logo=_logo(team),
        score=_int_or_none(competitor.get("score")),
    )


def _venue(value: Any) -> MatchVenue | None:
    venue = _as_dict(value)
    if not venue:
        return None
    address = _as_dict(venue.get("address"))
    return MatchVenue(
        name=_string(venue.get("fullName")) or _string(venue.get("name")),
        city=_string(address.get("city")) or _string(venue.get("city")),
    )


def _team_stats(boxscore: Any) -> list[TeamStats]:
    teams = _as_dict(boxscore).get("teams")
    if not isinstance(teams, list):
        return []
    results: list[TeamStats] = []
    for item in teams:
        item_dict = _as_dict(item)
        team = _as_dict(item_dict.get("team"))
        team_id = _string(team.get("id")) or _string(item_dict.get("teamId"))
        if not team_id:
            continue
        results.append(
            TeamStats(
                teamId=team_id,
                stats=_stats(item_dict.get("statistics")),
            )
        )
    return results


def _player_leaders(leaders: Any) -> list[PlayerLeaderCategory]:
    if not isinstance(leaders, list):
        return []
    categories: list[PlayerLeaderCategory] = []
    for category in leaders:
        category_dict = _as_dict(category)
        players = []
        for item in category_dict.get("leaders") or []:
            leader = _as_dict(item)
            athlete = _as_dict(leader.get("athlete"))
            player_id = _string(athlete.get("id"))
            if not player_id:
                continue
            stats = _stats(leader.get("statistics"))
            players.append(
                MatchLeaderPlayer(
                    id=player_id,
                    name=_string(athlete.get("displayName")) or player_id,
                    position=_string(_as_dict(athlete.get("position")).get("displayName"))
                    or _string(_as_dict(athlete.get("position")).get("name")),
                    jersey=_string(athlete.get("jersey")),
                    espnUrl=_first_link(athlete.get("links")),
                    mainStat=_string(leader.get("displayValue"))
                    or _string(leader.get("value"))
                    or (stats[0].value if stats else None),
                    stats=stats,
                )
            )
        categories.append(
            PlayerLeaderCategory(
                category=_string(category_dict.get("displayName"))
                or _string(category_dict.get("name"))
                or "Leaders",
                players=players,
            )
        )
    return categories


def _events(details: Any, commentary: Any) -> list[MatchEvent]:
    events: list[MatchEvent] = []
    for item in details if isinstance(details, list) else []:
        event = _event(item)
        if event is not None:
            events.append(event)
    for item in commentary if isinstance(commentary, list) else []:
        event = _event(item)
        if event is not None:
            events.append(event)
    return events


def _event(value: Any) -> MatchEvent | None:
    item = _as_dict(value)
    text = _string(item.get("text")) or _string(item.get("headline")) or _string(item.get("displayName"))
    if not text:
        return None
    event_type = item.get("type")
    type_text = _string(_as_dict(event_type).get("text")) or _string(event_type)
    time = _as_dict(item.get("time"))
    team = _as_dict(item.get("team"))
    return MatchEvent(
        minute=_string(time.get("displayValue")) or _string(item.get("clock")),
        type=type_text,
        text=text,
        team=_string(team.get("displayName")) or _string(team.get("name")),
    )


def _stats(value: Any) -> list[MatchStat]:
    if not isinstance(value, list):
        return []
    stats: list[MatchStat] = []
    for stat in value:
        stat_dict = _as_dict(stat)
        name = _string(stat_dict.get("name")) or _string(stat_dict.get("abbreviation"))
        if not name:
            continue
        stats.append(
            MatchStat(
                name=name,
                label=_string(stat_dict.get("displayName"))
                or _string(stat_dict.get("label"))
                or name,
                value=_string(stat_dict.get("displayValue"))
                or _string(stat_dict.get("value"))
                or "",
            )
        )
    return stats


def _logo(team: dict[str, Any]) -> str | None:
    logo = _string(team.get("logo"))
    if logo:
        return logo
    logos = team.get("logos")
    if isinstance(logos, list) and logos:
        return _string(_as_dict(logos[0]).get("href"))
    return None


def _first_link(links: Any) -> str | None:
    if isinstance(links, list) and links:
        return _string(_as_dict(links[0]).get("href"))
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
