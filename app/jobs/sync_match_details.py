from __future__ import annotations

import argparse
from datetime import date, timedelta

from app.core.firebase import get_firestore_client
from app.services.match_detail import EspnMatchDetailClient, FirestoreMatchDetailStore


DEFAULT_LEAGUES = (
    "fifa.world",
    "eng.1",
    "esp.1",
    "ita.1",
    "ger.1",
    "fra.1",
    "uefa.champions",
    "uefa.europa",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Warm due Goalio match detail Firestore docs.")
    parser.add_argument("--from-date", default=date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--to-date", default=(date.today() + timedelta(days=2)).isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--league", action="append", dest="leagues", help="ESPN league code. Repeat for multiple.")
    parser.add_argument("--max-matches", type=int, default=30, help="Safety cap for one run.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = EspnMatchDetailClient()
    store = FirestoreMatchDetailStore(get_firestore_client())
    leagues = tuple(args.leagues or DEFAULT_LEAGUES)
    warmed = 0
    checked = 0
    for league in leagues:
        if checked >= args.max_matches:
            break
        schedule = client.schedule(league, date=None, from_date=args.from_date, to_date=args.to_date)
        for match in schedule.matches:
            if checked >= args.max_matches:
                break
            checked += 1
            if not store.is_due(match.league, match.matchId):
                continue
            client.cached_detail(match.league, match.matchId, store)
            warmed += 1
    print(f"checked={checked} warmed={warmed}")


if __name__ == "__main__":
    main()
