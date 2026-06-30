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

```text
GET /api/v1/football/teams?limit=100&cursor=...
GET /api/v1/football/players?limit=100&cursor=...
GET /api/v1/football/teams/search?q=arsenal
GET /api/v1/football/players/search?q=messi
```

## Test protected routes in Swagger

Production requests must always use a Firebase ID token. For local Swagger testing:

1. Set `APP_ENV=development` and `ALLOW_DEV_AUTH=true` in the ignored local `.env` file.
2. Fully restart Uvicorn so configuration and Firebase clients are reloaded.
3. Open `/docs`; missing credentials use the isolated local user `swagger-user`.
4. To test multiple users, authorize with `dev:second-user` without the `Bearer` prefix.

Development tokens are accepted only when both `APP_ENV=development` and
`ALLOW_DEV_AUTH=true`.
