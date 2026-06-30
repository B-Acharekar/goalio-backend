# Goalio API

FastAPI service for Firebase-authenticated Goalio profiles and football search.

## Run locally

1. Create and activate a virtual environment.
2. Install: `pip install -r requirements-dev.txt`
3. Set `GOOGLE_APPLICATION_CREDENTIALS` to a Firebase service-account JSON file.
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

The included `Procfile` contains the same start command. Configure these environment variables
on the host:

```text
APP_ENV=production
FIREBASE_PROJECT_ID=goalio-c42bc
ALLOWED_ORIGINS=https://your-web-client.example
ALLOW_DEV_AUTH=false
```

For Firebase credentials, use the host's Application Default Credentials, set
`GOOGLE_APPLICATION_CREDENTIALS` to a mounted secret file, or set the complete service-account
JSON as the secret `FIREBASE_SERVICE_ACCOUNT_JSON`. Never commit the service-account JSON.

After deployment, verify `GET /health` returns `{"status":"ok"}`, then set the Android Remote
Config key `backend_base_url` to the deployment's HTTPS origin.

## Test protected routes in Swagger

Production requests must always use a Firebase ID token. For local Swagger testing:

1. Set `APP_ENV=development` and `ALLOW_DEV_AUTH=true` in the ignored local `.env` file.
2. Fully restart Uvicorn so configuration and Firebase clients are reloaded.
3. Open `/docs` and call the profile and football endpoints normally. Missing credentials use the isolated local user `swagger-user`.
4. To test multiple users, click **Authorize** and enter a token such as `dev:second-user` (do not type the `Bearer` prefix).

Development tokens are accepted only when both `APP_ENV=development` and
`ALLOW_DEV_AUTH=true`.
