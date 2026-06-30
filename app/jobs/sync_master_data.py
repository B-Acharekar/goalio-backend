import argparse
from datetime import date

import httpx

from app.core.config import get_settings
from app.core.firebase import get_firestore_client
from app.services.master_data import (
    COMPETITIONS,
    COMPETITIONS_BY_ID,
    ApiFootballError,
    ApiFootballClient,
    EspnFootballClient,
    EspnFootballError,
    FallbackFootballClient,
    FirestoreMasterDataStore,
    MasterDataSyncService,
    is_sync_due,
)


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Sync Goalio football master data")
    parser.add_argument(
        "--season",
        type=int,
        default=settings.football_season,
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=settings.football_sync_max_requests,
    )
    parser.add_argument(
        "--competition",
        action="append",
        type=int,
        choices=sorted(COMPETITIONS_BY_ID),
        help="Competition ID to sync; repeat for multiple. Defaults to all six.",
    )
    parser.add_argument(
        "--due-only",
        action="store_true",
        help="Sync only from seven days before until fourteen days after season start.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    if args.max_requests < 1:
        raise SystemExit("--max-requests must be at least 1")
    api_key = settings.api_football_key.strip()
    if not api_key:
        raise SystemExit("API_FOOTBALL_KEY is required")

    selected = (
        [COMPETITIONS_BY_ID[item] for item in args.competition]
        if args.competition
        else list(COMPETITIONS)
    )
    api_football = ApiFootballClient(
        api_key,
        request_interval_seconds=settings.football_request_interval_seconds,
        max_requests=settings.api_football_max_requests,
    )
    api = FallbackFootballClient(
        EspnFootballClient(
            request_interval_seconds=settings.espn_request_interval_seconds,
        ),
        api_football,
    )
    try:
        store = FirestoreMasterDataStore(get_firestore_client())
        sync = MasterDataSyncService(api, store)
        remaining = args.max_requests
        today = date.today()
        for competition in selected:
            if remaining <= 0:
                break
            try:
                if args.due_only:
                    season_start = api.season_start(competition.id, args.season)
                    remaining -= 1
                    if not is_sync_due(season_start, today):
                        print(f"skip {competition.name}: season start {season_start or 'unavailable'}")
                        continue
                result = sync.sync_competition(competition, args.season, remaining)
                remaining -= result.requests_used
                print(
                    f"{competition.name}: processed={result.teams_processed} "
                    f"requests={result.requests_used} completed={result.completed}"
                )
            except (ApiFootballError, EspnFootballError, httpx.HTTPError) as error:
                print(f"stop {competition.name}: {error}")
                continue
    finally:
        api.close()


if __name__ == "__main__":
    main()
