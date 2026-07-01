from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Protocol

import httpx
from fastapi import HTTPException, status
from firebase_admin import firestore
from google.cloud.firestore_v1 import Client

from app.schemas.matches import (
    MatchDetail,
    MatchEvent,
    MatchLeaderPlayer,
    MatchOfficial,
    MatchStat,
    MatchTeam,
    MatchVenue,
    MatchWeather,
    LineupPlayer,
    PlayerLeaderCategory,
    ScoreboardMatch,
    ScoreboardResponse,
    StandingTeam,
    StandingsResponse,
    TeamLineup,
    TeamStats,
    WinProbability,
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


class MatchDetailStore(Protocol):
    def get(self, league: str, event_id: str) -> MatchDetail | None: ...

    def is_due(self, league: str, event_id: str) -> bool: ...

    def write_if_changed(self, detail: MatchDetail) -> bool: ...


class FirestoreMatchDetailStore:
    collection_name = "match_details"

    def __init__(self, client: Client):
        self.client = client

    def _ref(self, league: str, event_id: str):
        return self.client.collection(self.collection_name).document(_match_doc_id(league, event_id))

    def get(self, league: str, event_id: str) -> MatchDetail | None:
        snapshot = self._ref(league, event_id).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        return MatchDetail(**{key: value for key, value in data.items() if not key.startswith("_")})

    def is_due(self, league: str, event_id: str) -> bool:
        snapshot = self._ref(league, event_id).get()
        if not snapshot.exists:
            return True
        data = snapshot.to_dict() or {}
        next_refresh = data.get("_nextRefreshAt")
        if next_refresh is None:
            return False
        if hasattr(next_refresh, "timestamp"):
            return datetime.now(timezone.utc) >= next_refresh
        parsed = _parse_datetime(_string(next_refresh))
        return parsed is not None and datetime.now(timezone.utc) >= parsed

    def write_if_changed(self, detail: MatchDetail) -> bool:
        payload = _detail_payload(detail)
        detail_hash = _stable_hash(payload)
        ref = self._ref(detail.league, detail.matchId)
        snapshot = ref.get()
        current_hash = (snapshot.to_dict() or {}).get("_hash") if snapshot.exists else None
        if current_hash == detail_hash:
            return False
        ref.set(
            {
                **payload,
                "_hash": detail_hash,
                "_nextRefreshAt": _next_refresh_at(detail),
                "_updatedAt": firestore.SERVER_TIMESTAMP,
            }
        )
        return True


class EspnMatchDetailClient:
    def __init__(self, base_url: str = ESPN_SUMMARY_BASE_URL, timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def cached_detail(self, league: str, event_id: str, store: MatchDetailStore) -> MatchDetail:
        cached = store.get(league, event_id)
        if cached is not None and cached.lineups and not store.is_due(league, event_id):
            return cached
        fresh = self.detail(league, event_id)
        store.write_if_changed(fresh)
        return store.get(league, event_id) or fresh

    def detail(self, league: str, event_id: str) -> MatchDetail:
        detail = self.espn_detail(league, event_id)
        if not detail.lineups:
            lineups = self._lineups_from_fallbacks(league, event_id, detail)
            if lineups:
                detail.lineups = lineups
        return detail

    def espn_detail(self, league: str, event_id: str) -> MatchDetail:
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

    def espn_lineups(self, league: str, event_id: str) -> list[TeamLineup]:
        return self._lineups_from_espn_hidden_api(league, event_id)

    def google_lineups(self, league: str, event_id: str, detail: MatchDetail | None = None) -> list[TeamLineup]:
        return self._lineups_from_google_sports(league, event_id, detail)

    def yahoo_lineups(self, league: str, event_id: str, detail: MatchDetail | None = None) -> list[TeamLineup]:
        return self._lineups_from_yahoo_sports(league, event_id, detail)

    def _lineups_from_fallbacks(self, league: str, event_id: str, detail: MatchDetail) -> list[TeamLineup]:
        return (
            self._lineups_from_espn_hidden_api(league, event_id)
            or self._lineups_from_google_sports(league, event_id)
            or self._lineups_from_espn_match_page(league, event_id)
            or self._squad_lineups(detail)
        )

    def _squad_lineups(self, detail: MatchDetail) -> list[TeamLineup]:
        lineups = []
        for team in (detail.homeTeam, detail.awayTeam):
            if team is None:
                continue
            try:
                response = httpx.get(
                    f"{self.base_url}/{detail.league}/teams/{team.id}/roster",
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                continue
            players = [_lineup_player({"athlete": athlete, "substitute": True}) for athlete in payload.get("athletes") or []]
            players = [player for player in players if player is not None]
            coach = _coach_name(payload.get("coach")) or _coach_name(payload.get("coaches"))
            if players or coach:
                lineups.append(
                    TeamLineup(
                        teamId=team.id,
                        teamName=team.shortName or team.name,
                        formation=None,
                        coach=coach,
                        starters=[],
                        substitutes=players[:30],
                    )
                )
        return lineups

    def _lineups_from_espn_hidden_api(self, league: str, event_id: str) -> list[TeamLineup]:
        urls = (
            f"{self.base_url}/{league}/summary",
            f"https://site.web.api.espn.com/apis/common/v3/sports/soccer/{league}/summary",
        )
        for url in urls:
            try:
                response = httpx.get(url, params={"event": event_id, "region": "us", "lang": "en"}, timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                continue
            competition = _competition(payload)
            lineups = _lineups(_as_dict(payload.get("boxscore")), competition.get("competitors") or [])
            if lineups:
                return lineups
        return []

    def _lineups_from_google_sports(self, league: str, event_id: str, detail: MatchDetail | None = None) -> list[TeamLineup]:
        # Best-effort fallback. Google Sports pages are not a stable API, so keep this read-only and fail closed.
        try:
            response = httpx.get(
                "https://www.google.com/search",
                params={"q": _lineup_search_query(league, event_id, detail)},
                headers={"User-Agent": "Mozilla/5.0 GoalioBot/1.0"},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        return _lineups_from_html(response.text)

    def _lineups_from_espn_match_page(self, league: str, event_id: str) -> list[TeamLineup]:
        try:
            response = httpx.get(
                f"https://www.espn.com/soccer/match/_/gameId/{event_id}",
                headers={"User-Agent": "Mozilla/5.0 GoalioBot/1.0"},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        return _lineups_from_html(response.text)

    def _lineups_from_yahoo_sports(self, league: str, event_id: str, detail: MatchDetail | None = None) -> list[TeamLineup]:
        headers = {"User-Agent": "Mozilla/5.0 GoalioBot/1.0"}
        try:
            search = httpx.get(
                "https://search.yahoo.com/search",
                params={"p": f"site:sports.yahoo.com {_lineup_search_query(league, event_id, detail)}"},
                headers=headers,
                timeout=self.timeout,
            )
            search.raise_for_status()
        except httpx.HTTPError:
            return []
        links = [
            link.replace("&amp;", "&")
            for link in re.findall(r'https?://sports\.yahoo\.com/[^"<> ]+', search.text)
        ]
        for link in dict.fromkeys(links):
            try:
                response = httpx.get(link, headers=headers, timeout=self.timeout, follow_redirects=True)
                response.raise_for_status()
            except httpx.HTTPError:
                continue
            lineups = _lineups_from_html(response.text)
            if lineups:
                return lineups
        return []

    def scoreboard(self, league: str, dates: str | None = None) -> ScoreboardResponse:
        _validate_league(league)
        validate_scoreboard_dates(dates)
        return self._scoreboard(league, dates, schedule_date=None)

    def standings(self, league: str, season: int | None = None) -> StandingsResponse:
        _validate_league(league)
        params = {"season": season} if season else None
        try:
            response = httpx.get(
                f"https://site.web.api.espn.com/apis/v2/sports/soccer/{league}/standings",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "League standings not found") from exc
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "ESPN standings are temporarily unavailable",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "ESPN standings are temporarily unavailable",
            ) from exc
        return normalize_espn_standings(league, response.json(), season=season)

    def schedule(
        self,
        league: str,
        date: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> ScoreboardResponse:
        _validate_league(league)
        dates = schedule_dates_to_espn(date, from_date, to_date)
        return self._scoreboard(league, dates, schedule_date=date or _range_date(from_date, to_date))

    def _scoreboard(
        self,
        league: str,
        dates: str | None,
        schedule_date: str | None,
    ) -> ScoreboardResponse:
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
        return normalize_espn_scoreboard(league, response.json(), schedule_date=schedule_date)


def normalize_espn_scoreboard(
    league: str,
    payload: dict[str, Any],
    schedule_date: str | None = None,
) -> ScoreboardResponse:
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
                name=_string(event_dict.get("name")),
                shortName=_string(event_dict.get("shortName")),
                status=_string(status_type.get("abbreviation")) or _string(status_type.get("shortDetail")),
                statusDescription=_string(status_type.get("detail"))
                or _string(status_type.get("description")),
                state=_string(status_type.get("state")),
                kickoff=_string(competition.get("date")) or _string(event_dict.get("date")),
                homeTeam=_team(home),
                awayTeam=_team(away),
                venue=_venue(competition.get("venue")),
                detailApi=f"/api/matches/{league}/{match_id}/detail",
            )
        )
    return ScoreboardResponse(league=league, date=schedule_date, matches=matches)


def normalize_espn_standings(
    league: str,
    payload: dict[str, Any],
    season: int | None = None,
) -> StandingsResponse:
    teams: list[StandingTeam] = []
    groups: list[str] = []

    def add_entries(entries: Any, group_name: str | None, stage_name: str | None) -> None:
        if group_name and group_name not in groups:
            groups.append(group_name)
        for entry in entries if isinstance(entries, list) else []:
            standing = _standing_team(entry, group_name, stage_name)
            if standing is not None:
                teams.append(standing)

    standings = payload.get("standings")
    if isinstance(standings, list):
        for group in standings:
            group_dict = _as_dict(group)
            group_name = _string(group_dict.get("name")) or _string(group_dict.get("displayName"))
            stage_name = _string(group_dict.get("abbreviation")) or _string(group_dict.get("shortName"))
            add_entries(group_dict.get("entries"), group_name, stage_name)
            for child in group_dict.get("children") or []:
                child_dict = _as_dict(child)
                child_name = _string(child_dict.get("name")) or _string(child_dict.get("displayName")) or group_name
                child_stage = _string(child_dict.get("abbreviation")) or _string(child_dict.get("shortName")) or stage_name
                add_entries(child_dict.get("entries"), child_name, child_stage)
    elif isinstance(standings, dict):
        standings_dict = _as_dict(standings)
        add_entries(standings_dict.get("entries"), _string(standings_dict.get("name")), _string(standings_dict.get("abbreviation")))
        for child in standings_dict.get("children") or []:
            child_dict = _as_dict(child)
            add_entries(
                child_dict.get("entries"),
                _string(child_dict.get("name")) or _string(child_dict.get("displayName")),
                _string(child_dict.get("abbreviation")) or _string(child_dict.get("shortName")),
            )

    children = payload.get("children")
    for child in children if isinstance(children, list) else []:
        child_dict = _as_dict(child)
        child_name = _string(child_dict.get("name")) or _string(child_dict.get("displayName"))
        child_stage = _string(child_dict.get("abbreviation")) or _string(child_dict.get("shortName"))
        standings_dict = _as_dict(child_dict.get("standings"))
        add_entries(standings_dict.get("entries") or child_dict.get("entries"), child_name, child_stage)

    deduped: dict[tuple[str, str | None], StandingTeam] = {}
    for team in teams:
        deduped[(team.teamId, team.group)] = team
    return StandingsResponse(
        league=league,
        season=season or _int_or_none(_as_dict(payload.get("season")).get("year")),
        groups=groups,
        teams=sorted(
            deduped.values(),
            key=lambda item: ((item.group or ""), item.rank if item.rank is not None else 999, item.name),
        ),
    )


def normalize_espn_summary(league: str, event_id: str, payload: dict[str, Any]) -> MatchDetail:
    competition = _competition(payload)
    competitors = competition.get("competitors") or []
    home = _find_competitor(competitors, "home") or (competitors[0] if competitors else None)
    away = _find_competitor(competitors, "away") or (competitors[1] if len(competitors) > 1 else None)
    status_type = _as_dict(_as_dict(competition.get("status")).get("type"))
    article = _as_dict(payload.get("article"))
    boxscore = _as_dict(payload.get("boxscore"))
    game_info = _as_dict(payload.get("gameInfo"))

    detail = MatchDetail(
        matchId=str(event_id),
        league=league,
        status=_string(status_type.get("abbreviation")) or _string(status_type.get("shortDetail")),
        statusDescription=_string(status_type.get("detail"))
        or _string(status_type.get("description")),
        kickoff=_string(competition.get("date")) or _string(_as_dict(payload.get("header")).get("date")),
        homeTeam=_team(home),
        awayTeam=_team(away),
        venue=_venue(competition.get("venue")) or _venue(game_info.get("venue")),
        officials=_officials(competition.get("officials"))
        or _officials(game_info.get("officials"))
        or _officials(payload.get("officials")),
        weather=_weather(competition.get("weather"))
        or _weather(game_info.get("weather"))
        or _weather(payload.get("weather")),
        teamStats=_team_stats(boxscore),
        playerLeaders=_player_leaders(payload.get("leaders")),
        lineups=_lineups(boxscore, competitors),
        events=_events(competition.get("details"), payload.get("commentary")),
        summary=_string(article.get("story")) or _string(article.get("description")),
    )
    
    from app.services.probability import calculate_win_probability
    detail.winProbability = _win_probability(payload) or calculate_win_probability(detail)
    
    return detail


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


def schedule_dates_to_espn(
    date: str | None,
    from_date: str | None,
    to_date: str | None,
) -> str | None:
    has_single = date is not None
    has_range = from_date is not None or to_date is not None
    if has_single and has_range:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Use either date or from/to, not both",
        )
    if has_single:
        return _iso_date_to_espn(date, "date")
    if from_date is None and to_date is None:
        return None
    if from_date is None or to_date is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Both from and to are required for a date range",
        )
    return f"{_iso_date_to_espn(from_date, 'from')}-{_iso_date_to_espn(to_date, 'to')}"


def _iso_date_to_espn(value: str, field: str) -> str:
    parts = value.split("-")
    valid = (
        len(parts) == 3
        and len(parts[0]) == 4
        and len(parts[1]) == 2
        and len(parts[2]) == 2
        and all(part.isdigit() for part in parts)
    )
    if not valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{field} must be YYYY-MM-DD",
        )
    return "".join(parts)


def _range_date(from_date: str | None, to_date: str | None) -> str | None:
    if from_date is None and to_date is None:
        return None
    return f"{from_date}/{to_date}"


def _competition(payload: dict[str, Any]) -> dict[str, Any]:
    header = _as_dict(payload.get("header"))
    competitions = header.get("competitions")
    if isinstance(competitions, list) and competitions:
        return _as_dict(competitions[0])
    return {}


def _match_doc_id(league: str, event_id: str) -> str:
    return f"{league}_{event_id}".replace("/", "_")


def _detail_payload(detail: MatchDetail) -> dict[str, Any]:
    return detail.model_dump(mode="json")


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _next_refresh_at(detail: MatchDetail) -> datetime | None:
    now = datetime.now(timezone.utc)
    kickoff = _parse_datetime(detail.kickoff)
    if kickoff is None:
        return None
    if detail.statusDescription and detail.statusDescription.casefold() in {"ft", "full time", "final"}:
        return None
    if detail.status and detail.status.casefold() in {"ft", "aet", "pen"}:
        return None
    windows = (
        kickoff - timedelta(hours=24),
        kickoff - timedelta(minutes=30),
        kickoff,
    )
    for window in windows:
        if now < window:
            return window
    return None


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


def _standing_team(entry: Any, group_name: str | None, stage_name: str | None) -> StandingTeam | None:
    entry_dict = _as_dict(entry)
    team = _as_dict(entry_dict.get("team"))
    team_id = _string(team.get("id")) or _string(entry_dict.get("id"))
    if not team_id:
        return None
    stats = {
        (_string(stat.get("name")) or _string(stat.get("abbreviation")) or "").casefold(): stat
        for stat in (_as_dict(item) for item in entry_dict.get("stats") or [])
    }

    def stat_int(*names: str) -> int | None:
        for name in names:
            stat = stats.get(name.casefold())
            value = _int_or_none(_as_dict(stat).get("value"))
            if value is not None:
                return value
            display_value = _int_or_none(_as_dict(stat).get("displayValue"))
            if display_value is not None:
                return display_value
        return None

    return StandingTeam(
        rank=stat_int("rank", "overall", "position") or _int_or_none(entry_dict.get("rank")),
        teamId=team_id,
        name=_string(team.get("displayName")) or _string(team.get("name")) or team_id,
        abbreviation=_string(team.get("abbreviation")) or _string(team.get("shortDisplayName")),
        logo=_logo(team),
        group=group_name,
        stage=stage_name,
        played=stat_int("gamesPlayed", "gamesplayed", "gp"),
        wins=stat_int("wins", "w"),
        draws=stat_int("ties", "draws", "d", "t"),
        losses=stat_int("losses", "l"),
        goalsFor=stat_int("pointsFor", "goalsFor", "gf", "f"),
        goalsAgainst=stat_int("pointsAgainst", "goalsAgainst", "ga", "a"),
        goalDifference=stat_int("pointDifferential", "goalDifference", "gd", "diff"),
        points=stat_int("points", "pts"),
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


def _win_probability(payload: dict[str, Any]) -> WinProbability | None:
    candidate = _find_probability_candidate(payload)
    if candidate is None:
        return None
    home = _probability_value(candidate, "homeWinPercentage", "homeWinProbability", "home", "homeTeam")
    away = _probability_value(candidate, "awayWinPercentage", "awayWinProbability", "away", "awayTeam")
    draw = _probability_value(candidate, "drawPercentage", "tiePercentage", "drawProbability", "tieProbability", "draw", "tie")
    if home is None or away is None:
        return None
    if draw is None:
        draw = max(0, 100 - home - away)
    total = home + away + draw
    if total != 100 and total > 0:
        home = round(home / total * 100)
        away = round(away / total * 100)
        draw = max(0, 100 - home - away)
    return WinProbability(
        homeWinPercentage=max(0, min(100, home)),
        awayWinPercentage=max(0, min(100, away)),
        drawPercentage=max(0, min(100, draw)),
    )


def _find_probability_candidate(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        lowered = {str(key).casefold(): key for key in value}
        if any("probability" in key or "percentage" in key for key in lowered):
            home = _probability_value(value, "homeWinPercentage", "homeWinProbability", "home", "homeTeam")
            away = _probability_value(value, "awayWinPercentage", "awayWinProbability", "away", "awayTeam")
            if home is not None and away is not None:
                return value
        for key in ("winProbability", "winprobability", "probabilities", "predictor", "odds"):
            child_key = lowered.get(key.casefold())
            if child_key is not None:
                found = _find_probability_candidate(value.get(child_key))
                if found is not None:
                    return found
        for child in value.values():
            found = _find_probability_candidate(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_probability_candidate(child)
            if found is not None:
                return found
    return None


def _probability_value(value: dict[str, Any], *keys: str) -> int | None:
    normalized = {str(key).casefold(): item for key, item in value.items()}
    for key in keys:
        raw = normalized.get(key.casefold())
        if isinstance(raw, dict):
            raw = raw.get("value") or raw.get("percentage") or raw.get("displayValue")
        parsed = _percent(raw)
        if parsed is not None:
            return parsed
    return None


def _percent(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if match is None:
            return None
        number = float(match.group(0))
    if 0 <= number <= 1:
        number *= 100
    if number < 0:
        return None
    return int(round(number))


def _officials(value: Any) -> list[MatchOfficial]:
    if not isinstance(value, list):
        return []
    officials: list[MatchOfficial] = []
    for item in value:
        item_dict = _as_dict(item)
        name = (
            _string(item_dict.get("displayName"))
            or _string(item_dict.get("fullName"))
            or _string(item_dict.get("name"))
        )
        role = (
            _string(item_dict.get("role"))
            or _string(item_dict.get("position"))
            or _string(item_dict.get("type"))
        )
        if name or role:
            officials.append(MatchOfficial(name=name, role=role))
    return officials


def _weather(value: Any) -> MatchWeather | None:
    weather = _as_dict(value)
    if not weather:
        return None
    temperature = (
        _string(weather.get("temperature"))
        or _string(weather.get("temperatureDisplayValue"))
        or _string(weather.get("highTemperature"))
    )
    condition = (
        _string(weather.get("condition"))
        or _string(weather.get("displayName"))
        or _string(weather.get("shortDisplayName"))
    )
    display_value = (
        _string(weather.get("displayValue"))
        or " ".join(item for item in [temperature, condition] if item)
        or None
    )
    if not display_value and not temperature and not condition:
        return None
    return MatchWeather(
        displayValue=display_value,
        temperature=temperature,
        condition=condition,
    )


def _lineups(boxscore: dict[str, Any], competitors: list[Any]) -> list[TeamLineup]:
    raw_teams = boxscore.get("teams")
    if not isinstance(raw_teams, list):
        return []
    competitor_by_id = {
        team_id: _as_dict(competitor)
        for competitor in competitors
        if (team_id := _string(_as_dict(_as_dict(competitor).get("team")).get("id")) or _string(_as_dict(competitor).get("id")))
    }
    lineups: list[TeamLineup] = []
    for item in raw_teams:
        team_block = _as_dict(item)
        team = _as_dict(team_block.get("team"))
        team_id = _string(team.get("id")) or _string(team_block.get("teamId"))
        competitor = competitor_by_id.get(team_id or "", {})
        lineup_source = _as_dict(team_block.get("lineup")) or team_block
        athletes = (
            lineup_source.get("athletes")
            or lineup_source.get("players")
            or lineup_source.get("roster")
            or lineup_source.get("entries")
            or team_block.get("athletes")
            or team_block.get("players")
            or team_block.get("roster")
            or []
        )
        if not athletes:
            for stat_group in team_block.get("statistics") or []:
                group_dict = _as_dict(stat_group)
                athletes = group_dict.get("athletes") or group_dict.get("players") or []
                if athletes:
                    break
        players = []
        for player in athletes if isinstance(athletes, list) else []:
            parsed = _lineup_player(player)
            if parsed is not None:
                players.append(parsed)
        starters = [player for player in players if player.starter and not player.substitute]
        substitutes = [player for player in players if player.substitute or not player.starter]
        coach = (
            _coach_name(team_block.get("coach"))
            or _coach_name(team_block.get("coaches"))
            or _coach_name(competitor.get("coach"))
            or _coach_name(competitor.get("coaches"))
        )
        if players or coach or team_id:
            lineups.append(
                TeamLineup(
                    teamId=team_id,
                    teamName=_string(team.get("displayName")) or _string(team.get("name")),
                    formation=_string(lineup_source.get("formation")) or _string(team_block.get("formation")),
                    coach=coach,
                    starters=starters,
                    substitutes=substitutes,
                )
            )
    return lineups


def _lineup_player(value: Any) -> LineupPlayer | None:
    item = _as_dict(value)
    athlete = _as_dict(item.get("athlete")) or _as_dict(item.get("player")) or item
    name = _string(athlete.get("displayName")) or _string(athlete.get("fullName")) or _string(athlete.get("name"))
    if not name:
        return None
    starter = _truthy(item.get("starter")) or _truthy(item.get("isStarter")) or _truthy(item.get("starting"))
    substitute = _truthy(item.get("substitute")) or _truthy(item.get("isSubstitute")) or _truthy(item.get("bench"))
    lineup_status = (_string(item.get("lineupStatus")) or _string(item.get("status")) or "").casefold()
    if lineup_status in {"starter", "starting", "starting xi"}:
        starter = True
    if lineup_status in {"substitute", "bench"}:
        substitute = True
    position = _as_dict(athlete.get("position")) or _as_dict(item.get("position"))
    formation_place = _string(item.get("formationPlace")) or _string(item.get("formation_place"))
    headshot = _as_dict(athlete.get("headshot")) or _as_dict(item.get("headshot"))
    x = _float_or_none(item.get("x"))
    y = _float_or_none(item.get("y"))
    return LineupPlayer(
        id=_string(athlete.get("id")) or _string(item.get("id")),
        name=name,
        position=_string(position.get("abbreviation"))
        or _string(position.get("displayName"))
        or _string(position.get("name"))
        or _string(item.get("position")),
        jersey=_string(athlete.get("jersey")) or _string(item.get("jersey")),
        starter=starter,
        captain=bool(item.get("captain") or item.get("isCaptain")),
        substitute=substitute,
        formationPlace=formation_place,
        role=_string(position.get("displayName")) or _string(position.get("name")),
        photo=_string(headshot.get("href")) or _string(athlete.get("photo")) or _string(item.get("photo")),
        x=x,
        y=y,
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "starter", "starting", "substitute", "bench"}
    return False


def _coach_name(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            name = _coach_name(item)
            if name:
                return name
        return None
    coach = _as_dict(value)
    return (
        _string(coach.get("displayName"))
        or _string(coach.get("fullName"))
        or _string(coach.get("name"))
    )


def _lineups_from_html(html: str) -> list[TeamLineup]:
    for json_text in _embedded_json_candidates(html):
        try:
            payload = json.loads(json_text)
        except ValueError:
            continue
        lineups = _lineups_from_any_json(payload)
        if lineups:
            return lineups
    return []


def _lineup_search_query(league: str, event_id: str, detail: MatchDetail | None) -> str:
    teams = [
        team.shortName or team.name
        for team in (detail.homeTeam, detail.awayTeam) if team is not None
    ] if detail else []
    return " ".join([*teams, "football lineups", league, event_id])


def _embedded_json_candidates(html: str) -> list[str]:
    candidates = []
    for pattern in (
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        r'<script[^>]+type="application/(?:ld\+)?json"[^>]*>(.*?)</script>',
        r"window\.__espnfitt__\s*=\s*({.*?});",
        r"window\['__espnfitt__'\]\s*=\s*({.*?});",
    ):
        candidates.extend(re.findall(pattern, html, flags=re.DOTALL))
    return candidates


def _lineups_from_any_json(value: Any) -> list[TeamLineup]:
    if isinstance(value, dict):
        if "boxscore" in value:
            competition = _competition(value)
            lineups = _lineups(_as_dict(value.get("boxscore")), competition.get("competitors") or [])
            if lineups:
                return lineups
        if "lineups" in value and isinstance(value["lineups"], list):
            lineups = _lineups({"teams": value["lineups"]}, [])
            if lineups:
                return lineups
        for child in value.values():
            lineups = _lineups_from_any_json(child)
            if lineups:
                return lineups
    elif isinstance(value, list):
        for child in value:
            lineups = _lineups_from_any_json(child)
            if lineups:
                return lineups
    return []


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


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
