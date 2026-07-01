import re
from app.schemas.matches import MatchDetail, WinProbability

def calculate_win_probability(match: MatchDetail) -> WinProbability | None:
    home_score = match.homeTeam.score if match.homeTeam and match.homeTeam.score is not None else 0
    away_score = match.awayTeam.score if match.awayTeam and match.awayTeam.score is not None else 0

    status_str = (match.status or "").upper()
    
    # Check if match is finished
    if "FT" in status_str or "FINAL" in status_str or "FULL TIME" in (match.statusDescription or "").upper():
        if home_score > away_score:
            return WinProbability(homeWinPercentage=100, awayWinPercentage=0, drawPercentage=0)
        elif away_score > home_score:
            return WinProbability(homeWinPercentage=0, awayWinPercentage=100, drawPercentage=0)
        else:
            return WinProbability(homeWinPercentage=0, awayWinPercentage=0, drawPercentage=100)

    # Parse elapsed minutes
    minutes_match = re.search(r'(\d+)', status_str)
    minutes_elapsed = int(minutes_match.group(1)) if minutes_match else 0
    
    # If scheduled (not started yet), don't have elapsed minutes, base at 0
    if not minutes_match and "SCHEDULED" in status_str:
        minutes_elapsed = 0

    time_factor = min(minutes_elapsed / 90.0, 1.0)
    
    home_chance = 50.0
    away_chance = 50.0

    # Score difference impact
    score_diff = home_score - away_score
    score_impact = score_diff * (10 + (30 * time_factor))
    home_chance += score_impact
    away_chance -= score_impact

    # Momentum from stats
    home_possession, away_possession = 50.0, 50.0
    home_shots, away_shots = 0.0, 0.0
    home_reds, away_reds = 0.0, 0.0

    if match.teamStats:
        for team_stat in match.teamStats:
            is_home = (match.homeTeam and team_stat.teamId == match.homeTeam.id)
            is_away = (match.awayTeam and team_stat.teamId == match.awayTeam.id)
            if not is_home and not is_away:
                continue

            for stat in team_stat.stats:
                val = 0.0
                try:
                    val_str = stat.value.replace("%", "").strip()
                    val = float(val_str)
                except ValueError:
                    pass

                name_lower = stat.name.lower()
                if "possession" in name_lower:
                    if is_home: home_possession = val
                    if is_away: away_possession = val
                elif "shots" in name_lower and "on" not in name_lower and "off" not in name_lower:
                    if is_home: home_shots = val
                    if is_away: away_shots = val
                elif "redcard" in name_lower or "red_card" in name_lower:
                    if is_home: home_reds = val
                    if is_away: away_reds = val

    # Apply momentum to base chance
    poss_diff = home_possession - away_possession
    home_chance += (poss_diff * 0.1)
    away_chance -= (poss_diff * 0.1)

    shots_diff = home_shots - away_shots
    home_chance += (shots_diff * 0.5)
    away_chance -= (shots_diff * 0.5)

    red_diff = home_reds - away_reds
    home_chance -= (red_diff * 15)
    away_chance += (red_diff * 15)

    # Draw probability
    draw_chance = 0.0
    if score_diff == 0:
        draw_chance = 20 + (30 * time_factor)  # Max ~50% late in game if tied
    elif abs(score_diff) == 1:
        draw_chance = 10 + (10 * time_factor)  # Max ~20% late in game if 1 goal diff

    home_chance = max(1.0, home_chance)
    away_chance = max(1.0, away_chance)
    draw_chance = max(0.0, draw_chance)

    total = home_chance + away_chance + draw_chance
    home_pct = int(round((home_chance / total) * 100))
    draw_pct = int(round((draw_chance / total) * 100))
    away_pct = 100 - home_pct - draw_pct

    return WinProbability(
        homeWinPercentage=home_pct,
        awayWinPercentage=away_pct,
        drawPercentage=draw_pct
    )
