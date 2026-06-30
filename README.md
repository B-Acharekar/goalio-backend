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

The included `Procfile` contains the same start command. Use `.env.production.example` as the
exact environment-variable template in Render:

```text
APP_ENV=production
FIREBASE_PROJECT_ID=goalio-c42bc
ALLOWED_ORIGINS=https://your-web-client.example
ALLOW_DEV_AUTH=false
GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/firebase-service-account.json
```

On Render, add a Secret File named `firebase-service-account.json`, paste the complete Firebase
service-account JSON as its contents, and add this environment variable:

```text
GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/firebase-service-account.json
```

Do not also set `FIREBASE_SERVICE_ACCOUNT_JSON`. That environment variable remains available as
an alternative for hosts without secret-file support. Never commit the service-account JSON.

After deployment, verify `GET /health` returns `{"status":"ok"}`, then set the Android Remote
Config key `backend_base_url` to the deployment's HTTPS origin.

## Football master-data sync

The sync imports these API-Football v3 competitions:

| Competition | API league ID |
| --- | ---: |
| Premier League | 39 |
| LaLiga | 140 |
| Serie A | 135 |
| Bundesliga | 78 |
| Ligue 1 | 61 |
| World Cup | 1 |

Set `API_FOOTBALL_KEY` as a secret environment variable. Do not put the real key in a committed
environment file. The importer maintains:

- `teams/{teamId}` for active clubs and national teams;
- `players/{playerId}`, deduplicated by the API-Football player ID;
- `team_players/{teamId_playerId}` for club and country membership;
- `master_data_sync/{competitionId_season}` for resumable progress.

Create one daily Render Cron Job using the same repository, environment variables, Firebase
Secret File, and build command as the web service. Use this command:

```text
python -m app.jobs.sync_master_data --season 2026 --due-only --max-requests 95
```

Suggested UTC schedule: `0 1 * * *`. The job spends six requests checking the competition season
dates, starts each competition seven days before its season, and resumes from the next team on
the following day when the request budget is exhausted. It keeps trying through fourteen days
after the published start date. Requests are spaced by `FOOTBALL_REQUEST_INTERVAL_SECONDS=6.2`
to stay below the free plan's 10-request-per-minute limit, and HTTP 429 responses are retried once.

For an immediate manual import, omit `--due-only`:

```text
python -m app.jobs.sync_master_data --season 2026 --max-requests 95
```

Run that command again on later days until every competition reports `completed=True`. Squad
responses provide player ID, name, age, position, and photo. `firstname`, `lastname`, and
`nationality` remain null unless a separate profile-enrichment import is added; fetching a profile
per player would exceed the 100-request daily plan.

The API subscription must provide the requested season. At the time this integration was tested,
the supplied free-plan key rejected season 2026 and reported access only to seasons 2022–2024.
Upgrade the API-Football plan before enabling the 2026 cron job.

## Test protected routes in Swagger

Production requests must always use a Firebase ID token. For local Swagger testing:

1. Set `APP_ENV=development` and `ALLOW_DEV_AUTH=true` in the ignored local `.env` file.
2. Fully restart Uvicorn so configuration and Firebase clients are reloaded.
3. Open `/docs` and call the profile and football endpoints normally. Missing credentials use the isolated local user `swagger-user`.
4. To test multiple users, click **Authorize** and enter a token such as `dev:second-user` (do not type the `Bearer` prefix).

Development tokens are accepted only when both `APP_ENV=development` and
`ALLOW_DEV_AUTH=true`.
