# Goalio API

FastAPI service for Firebase-authenticated Goalio profiles and football search.

## Run locally

1. Create and activate a virtual environment.
2. Copy `.env.example` to `.env` and set the absolute local service-account path.
3. Install: `pip install -r requirements-dev.txt`.
4. Enable **Anonymous** sign-in in Firebase Console -> Authentication -> Sign-in method.
5. Enable the [Cloud Firestore API](https://console.developers.google.com/apis/api/firestore.googleapis.com/overview?project=goalio-c42bc) and create the default Firestore database for project `goalio-c42bc`.
6. Run: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`.

Android emulators reach the host at `http://10.0.2.2:8000`. All `/api/v1` routes require
`Authorization: Bearer <Firebase ID token>`.

## Deploy without Docker

Use a Python 3.12 web service on Render, Railway, Heroku, or another buildpack-based host.

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips "*"`
- Health-check path: `/health`

Use `.env.production.example` as the Render environment-variable template. Add a Render Secret
File named `firebase-service-account.json` containing the complete Firebase service-account JSON,
then set:

```text
GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/firebase-service-account.json
```

Do not also set `FIREBASE_SERVICE_ACCOUNT_JSON`. Never commit the service-account JSON.

After deployment, verify `GET /health` returns `{"status":"ok"}`, then set the Android Remote
Config key `backend_base_url` to the deployment's HTTPS origin.

## Football master-data sync

The importer uses ESPN's public site JSON as its primary seed source and API-Football v3 as the
fallback when ESPN cannot provide a competition. The Android app never calls either provider;
it reads the resulting Firestore catalog through this backend.

| Competition | ESPN code | API-Football ID |
| --- | --- | ---: |
| Premier League | `eng.1` | 39 |
| LaLiga | `esp.1` | 140 |
| Serie A | `ita.1` | 135 |
| Bundesliga | `ger.1` | 78 |
| Ligue 1 | `fra.1` | 61 |
| World Cup | `fifa.world` | 1 |

Set `API_FOOTBALL_KEY` as a secret environment variable for fallback only. The importer maintains:

- `teams/{source_teamId}` for active clubs and national teams;
- `players/{source_playerId}`, deduplicated by ESPN athlete ID when ESPN is used;
- `team_players/{teamId_playerId}` for club and country membership;
- `master_data_sync/{competitionId_season}` for resumable progress and provider tracking.

Create one daily Render Cron Job using the same repository, environment variables, Firebase
Secret File, and build command as the web service:

```text
python -m app.jobs.sync_master_data --season 2026 --due-only --max-requests 250
```

Suggested UTC schedule: `0 1 * * *`. The job checks ESPN's published season window, starts each
competition seven days before that window, and resumes from the next team after interruption.
ESPN requests are spaced by `ESPN_REQUEST_INTERVAL_SECONDS=0.5`. If ESPN team data is unavailable,
API-Football fallback is limited to `API_FOOTBALL_MAX_REQUESTS=95` and paced at 6.2 seconds.

For an immediate manual import, omit `--due-only`:

```text
python -m app.jobs.sync_master_data --season 2026 --max-requests 250
```

ESPN is undocumented and can change without notice; Firestore remains the stable master-data
boundary. The API-Football free plan used during development rejected 2026 and allowed only
2022-2024, so it cannot serve as a 2026 fallback unless that plan is upgraded.

## Football catalog endpoints

All catalog endpoints require:

```http
Authorization: Bearer <Firebase ID token>
```

Catalog responses are paginated in visible UI chunks. The default page size is `6`; the max
accepted `limit` is `20`. For "load more", send the previous response's `nextCursor`.

| Endpoint URL | Request example | Response example |
| --- | --- | --- |
| `GET /api/v1/football/teams` | `GET /api/v1/football/teams?limit=6` | `{"items":[{"id":"espn_eng.1_359","name":"Arsenal","shortName":"Arsenal","competitionIds":[39],"imageUrl":"https://..."}],"nextCursor":"espn_eng.1_359"}` |
| `GET /api/v1/football/teams` | `GET /api/v1/football/teams?limit=6&cursor=espn_eng.1_359` | `{"items":[{"id":"espn_eng.1_363","name":"Chelsea","shortName":"Chelsea","competitionIds":[39],"imageUrl":"https://..."}],"nextCursor":"espn_eng.1_363"}` |
| `GET /api/v1/football/players` | `GET /api/v1/football/players?limit=6` | `{"items":[{"id":"espn_231182","name":"Kai Havertz","team":"Arsenal, Germany","competitionIds":[1,39],"imageUrl":"https://..."}],"nextCursor":"espn_231182"}` |
| `GET /api/v1/football/players` | `GET /api/v1/football/players?limit=6&cursor=espn_231182` | `{"items":[{"id":"espn_158023","name":"Mohamed Salah","team":"Liverpool, Egypt","competitionIds":[39],"imageUrl":"https://..."}],"nextCursor":"espn_158023"}` |
| `GET /api/v1/football/teams/search` | `GET /api/v1/football/teams/search?q=arsenal&limit=6` | `{"items":[{"id":"espn_eng.1_359","name":"Arsenal","shortName":"Arsenal","competitionIds":[39],"imageUrl":"https://..."}],"nextCursor":null}` |
| `GET /api/v1/football/players/search` | `GET /api/v1/football/players/search?q=messi&limit=6` | `{"items":[{"id":"espn_45843","name":"Lionel Messi","team":"Argentina","competitionIds":[1],"imageUrl":"https://..."}],"nextCursor":null}` |

## Match detail endpoint

The match detail endpoint uses ESPN's public `summary` JSON live and normalizes it for the app.
It does **not** write to Firebase yet. Later, Firebase caching/storage can be added behind the
same response contract.

Supported ESPN league codes:

| League | Code |
| --- | --- |
| World Cup | `fifa.world` |
| EPL | `eng.1` |
| LaLiga | `esp.1` |
| Serie A | `ita.1` |
| Bundesliga | `ger.1` |
| Ligue 1 | `fra.1` |
| MLS | `usa.1` |
| Champions League | `uefa.champions` |
| Europa League | `uefa.europa` |

| Endpoint URL | Request example | Response example |
| --- | --- | --- |
| `GET /api/v1/matches/{league}/schedule` | `GET /api/v1/matches/eng.1/schedule?date=2026-08-15` | `{"league":"eng.1","date":"2026-08-15","matches":[{"matchId":"123456","league":"eng.1","name":"Arsenal vs Chelsea","shortName":"ARS v CHE","status":"Scheduled","statusDescription":"Scheduled","state":"pre","kickoff":"2026-08-15T14:00Z","homeTeam":{"id":"359","name":"Arsenal","shortName":"Arsenal","abbreviation":"ARS","logo":"https://...","score":null},"awayTeam":{"id":"363","name":"Chelsea","shortName":"Chelsea","abbreviation":"CHE","logo":"https://...","score":null},"venue":{"name":"Emirates Stadium","city":"London"},"detailApi":"/api/matches/eng.1/123456/detail"}]}` |
| `GET /api/v1/matches/{league}/schedule` | `GET /api/v1/matches/eng.1/schedule?from=2026-08-01&to=2026-08-31` | Same response shape, with `date` set to `2026-08-01/2026-08-31`. |
| `GET /api/v1/matches/{league}/scoreboard` | `GET /api/v1/matches/fifa.world/scoreboard` | Lower-level ESPN-style scoreboard wrapper. Prefer `/schedule` for app code. |
| `GET /api/v1/matches/{league}/scoreboard` | `GET /api/v1/matches/eng.1/scoreboard?dates=20260614` | Same response shape, filtered by ESPN's `dates` parameter when ESPN supports it. |
| `GET /api/v1/matches/{league}/{eventId}/detail` | `GET /api/v1/matches/fifa.world/760422/detail` | See normalized response below. |
| `GET /api/matches/{league}/{eventId}/detail` | `GET /api/matches/eng.1/401695632/detail` | Compatibility alias for the same endpoint. Prefer `/api/v1/...` in app code. |

App flow:

1. Call schedule, for example `GET /api/v1/matches/eng.1/schedule?date=2026-08-15`.
2. Render match cards using `matches[].matchId`, score, teams, kickoff, and status.
3. When a user taps a match, call `GET /api/v1/matches/eng.1/{matchId}/detail`.

Live score schedules are shared through Firestore collection `match_scoreboards`. The API serves a
cached schedule for at most 120 seconds, then refreshes it from ESPN and writes it back. Run the
Procfile `worker` process (or invoke `python -m app.jobs.sync_live_scores` from Cloud Scheduler every
two minutes) so scores continue updating even when no app client is currently open. Match-detail
documents also remain due every 120 seconds after kickoff until the match reaches a final state.

Lineups use ESPN match detail first and call TheSportsDB only when ESPN has no complete starting XI.
Configure `THESPORTSDB_API_KEY` (the free/test key defaults to `123`), `THESPORTSDB_BASE_URL`, and
`THESPORTSDB_USE_V2_FALLBACK`. Provider mappings are stored at
`matches/{eventId}/providerMappings/theSportsDb`. In development/debug mode,
`GET /api/v1/debug/matches/{eventId}/lineup-provider` reports the masked provider chain.

For `/schedule`, use `date=YYYY-MM-DD` or `from=YYYY-MM-DD&to=YYYY-MM-DD`.
For lower-level `/scoreboard`, `dates` must be `YYYYMMDD` or `YYYYMMDD-YYYYMMDD`.
For example, use `20260609`, not `2026069`.

Normalized match detail response:

```json
{
  "matchId": "760422",
  "league": "fifa.world",
  "status": "FT",
  "statusDescription": "Full Time",
  "kickoff": "2026-06-14T17:00Z",
  "homeTeam": {
    "id": "481",
    "name": "Germany",
    "shortName": "Germany",
    "abbreviation": "GER",
    "logo": "https://...",
    "score": 7
  },
  "awayTeam": {
    "id": "11678",
    "name": "Curacao",
    "shortName": "Curacao",
    "abbreviation": "CUW",
    "logo": "https://...",
    "score": 1
  },
  "venue": {
    "name": "venue name",
    "city": "city"
  },
  "teamStats": [
    {
      "teamId": "481",
      "stats": [
        {
          "name": "possessionPct",
          "label": "Possession",
          "value": "65%"
        }
      ]
    }
  ],
  "playerLeaders": [
    {
      "category": "Shots",
      "players": [
        {
          "id": "231182",
          "name": "Kai Havertz",
          "position": "Forward",
          "jersey": "7",
          "espnUrl": "https://www.espn.com/soccer/player/_/id/231182/kai-havertz",
          "mainStat": "4",
          "stats": [
            {
              "name": "totalShots",
              "label": "Shots",
              "value": "4"
            }
          ]
        }
      ]
    }
  ],
  "events": [
    {
      "minute": "38'",
      "type": "Goal - Header",
      "text": "Nico Schlotterbeck Goal - Header",
      "team": "Germany"
    }
  ],
  "summary": "Match article/story text from ESPN"
}
```

## Test protected routes in Swagger

Production requests must always use a Firebase ID token. For local Swagger testing:

1. Set `APP_ENV=development` and `ALLOW_DEV_AUTH=true` in the ignored local `.env` file.
2. Fully restart Uvicorn so configuration and Firebase clients are reloaded.
3. Open `/docs`; missing credentials use the isolated local user `swagger-user`.
4. To test multiple users, authorize with `dev:second-user` without the `Bearer` prefix.

Development tokens are accepted only when both `APP_ENV=development` and
`ALLOW_DEV_AUTH=true`.
