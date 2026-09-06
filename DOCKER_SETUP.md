# LinkEasy Docker Setup and Handoff Guide

This guide explains how to run LinkEasy locally with Docker, PostgreSQL, Redis, the FastAPI API, Celery Worker, Celery Beat, and the Vite frontend. It is also intended for anyone who receives the source code and needs to set up the complete application.

## 1. Architecture

The repository contains two application parts:

| Component | Location | Responsibility |
| --- | --- | --- |
| FastAPI API | Repository root | Authentication, application APIs, OAuth callbacks, uploads |
| Celery Worker | Repository root | Background campaigns, social publishing, WhatsApp and LinkedIn jobs |
| Celery Beat | Repository root | Dispatches scheduled and recurring jobs |
| Frontend | `frontend/` | React/Vite browser application, built and served by Nginx |
| PostgreSQL | Docker Hub image `postgres:16-alpine` | Persistent application database |
| Redis | Docker Hub image `redis:7-alpine` | Celery broker, result backend, locks, and rate limiting |

The root `docker-compose.yml` starts PostgreSQL and Redis from Docker Hub, builds the API image from the local `Dockerfile`, and builds the frontend from `frontend/Dockerfile`. It starts separate API, Worker, Beat, and frontend containers.

## 2. Prerequisites

Install the following on the computer that will run the project:

- Docker Desktop with Docker Compose v2
- Git
- Node.js 20 or newer and npm, if running the frontend locally outside Docker
- A modern browser

Verify Docker:

```powershell
docker --version
docker compose version
```

## 3. Get the source code

```powershell
git clone <repository-url>
cd linkdin_automation
```

Do not copy live browser profiles, `.env` files, OAuth secrets, database dumps, or access tokens into the repository. The repository `.dockerignore` excludes `.env`, virtual environments, logs, and browser profiles from the image build.

## 4. Create the root environment file

Create a file named `.env` in the repository root. Do not commit it.

Minimum required values:

```env
DATABASE_URL=postgresql+asyncpg://linkeflow:linkeflow_password@postgres:5432/linkeasy
REDIS_URL=redis://redis:6379/0
JWT_SECRET=replace-with-a-long-random-secret
CREDENTIAL_ENCRYPTION_KEY=replace-with-64-lowercase-hex-characters
```

Generate secure values with PowerShell:

```powershell
python -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(48)); print('CREDENTIAL_ENCRYPTION_KEY=' + secrets.token_hex(32))"
```

The encryption key must remain unchanged after users connect accounts. Changing it makes previously stored OAuth and platform credentials unreadable and requires users to reconnect.

Recommended local development values:

```env
ENVIRONMENT=production
BACKEND_CORS_ORIGINS=http://localhost:5173,http://localhost:3000
PASSWORD_RESET_URL=http://localhost:5173/reset-password
DATA_DELETION_URL=http://localhost:5173/delete-confirm
FROM_EMAIL=noreply@example.com
PROFILE_STORAGE_DIR=/app/profiles
UPLOAD_DIR=/app/uploads/social
```

Optional integrations can be left empty. Add credentials only for the services that will be used:

```env
# YouTube
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REDIRECT_URI=http://localhost:8000/api/v1/social-scheduler/platforms/youtube/callback

# Instagram / Facebook
INSTAGRAM_APP_ID=
INSTAGRAM_APP_SECRET=
INSTAGRAM_REDIRECT_URI=http://localhost:8000/api/v1/social-scheduler/platforms/instagram/callback
FACEBOOK_APP_ID=
FACEBOOK_APP_SECRET=
FACEBOOK_REDIRECT_URI=http://localhost:8000/api/v1/social-scheduler/platforms/facebook/callback

# TikTok: TikTok does not accept localhost redirect URIs.
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_REDIRECT_URI=https://YOUR-PUBLIC-TUNNEL/api/v1/social-scheduler/platforms/tiktok/callback

# Gmail / Google OAuth web client
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/gmail/callback
GOOGLE_OAUTH_RETURN_URL=http://localhost:5173/app/account/gmail

# AI copy extraction
GROQ_API_KEY=
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_MODEL=qwen/qwen3.6-27b
```

For Compose, the values in `.env` are substituted into the `api`, `worker`, and `beat` services. The database and Redis URLs must use Docker service names (`postgres` and `redis`), not `localhost`, because containers reach one another over the Compose network.

## 5. PostgreSQL and Redis Docker Hub images

The Compose file already declares the official images:

```yaml
postgres:
  image: postgres:16-alpine

redis:
  image: redis:7-alpine
```

Pull them explicitly if desired:

```powershell
docker compose pull postgres redis
```

This downloads:

- PostgreSQL 16 Alpine from Docker Hub
- Redis 7 Alpine from Docker Hub

Check the downloaded images:

```powershell
docker image ls postgres redis
```

Docker will automatically pull an image during `docker compose up` if it is not already cached locally.

## 6. Build and start the complete backend stack

From the repository root:

```powershell
docker compose up -d --build
```

This starts five containers:

| Container | Port/role |
| --- | --- |
| `linkeflow-postgres` | Host port `5432` |
| `linkeflow-redis` | Host port `6379` |
| `linkeflow-api` | Host port `8000` |
| `linkeflow-worker` | Background jobs |
| `linkeflow-beat` | Scheduled job dispatcher |
| `linkeflow-frontend` | Host port `5173`, Nginx frontend |

The API container waits for healthy PostgreSQL and Redis containers. On startup it initializes the schema and runs database migrations.

Check status:

```powershell
docker compose ps
```

All services should be running. PostgreSQL and Redis should show healthy status.

Check logs:

```powershell
docker compose logs -f api
```

Worker logs:

```powershell
docker compose logs -f worker
```

Beat logs:

```powershell
docker compose logs -f beat
```

Open the API health endpoint:

```text
http://localhost:8000/health
```

## 7. Run the frontend

There are two frontend options.

### Option A: frontend in Docker

The Compose frontend service builds the Vite application and serves it through Nginx. It uses `/api/v1` as the browser API base URL and proxies `/api/` and `/uploads/` to the internal `api` service.

Start the full stack:

```powershell
docker compose up -d --build
```

Open the containerized application at:

```text
http://localhost:5173
```

### Option B: Vite development server

For hot reload during development, run the frontend outside Docker in a separate terminal:

```powershell
cd frontend
npm install
copy .env.example .env
```

Set `frontend/.env` to:

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_BACKEND_URL=http://localhost:8000
```

Start the frontend:

```powershell
npm run dev
```

Open the development frontend:

```text
http://localhost:5173
```

The frontend `VITE_API_BASE_URL` is embedded during the Vite build. If it changes, restart the Vite server or rebuild/redeploy the frontend. The Docker production build uses `/api/v1` because Nginx proxies that path to the API container.

## 8. Persistent data

The Compose file creates named Docker volumes:

```text
postgres_data
redis_data
```

These preserve database and Redis data when containers are recreated.

List volumes:

```powershell
docker volume ls
```

The API, Worker, and Beat containers currently bind-mount the source directory into `/app` for development. Browser profiles and uploaded media should be persisted deliberately if the application is used beyond local testing. The Dockerfile creates `/app/profiles`, but removing containers without a volume can remove browser sessions.

For a development profile volume, add a Compose volume mapping such as:

```yaml
volumes:
  - linkeasy_profiles:/app/profiles
  - linkeasy_uploads:/app/uploads/social
```

and declare:

```yaml
volumes:
  postgres_data:
  redis_data:
  linkeasy_profiles:
  linkeasy_uploads:
```

Never back up or share browser profile directories because they can contain active session cookies and device credentials.

## 9. OAuth callback setup

OAuth providers must point to the API container, not the frontend.

For YouTube:

```text
http://localhost:8000/api/v1/social-scheduler/platforms/youtube/callback
```

For Gmail, Google accepts localhost:

```text
http://localhost:8000/api/v1/gmail/callback
```

For TikTok, localhost is not accepted. Use a public HTTPS tunnel such as Cloudflare Tunnel or ngrok:

```text
https://YOUR-TUNNEL-DOMAIN/api/v1/social-scheduler/platforms/tiktok/callback
```

The provider console and the corresponding environment variable must match exactly, including protocol, host, path, and trailing slash behavior.

## 10. Production deployment

For production, do not expose PostgreSQL or Redis publicly unless there is a specific operational reason. Prefer a private Docker network and firewall rules that expose only the API/reverse proxy.

Use production values such as:

```env
DATABASE_URL=postgresql+asyncpg://<user>:<password>@postgres:5432/<database>
REDIS_URL=redis://redis:6379/0
BACKEND_CORS_ORIGINS=https://your-frontend-domain.example
PUBLIC_API_URL=https://your-api-domain.example
SOCIAL_OAUTH_RETURN_URL=https://your-frontend-domain.example/app/account
GOOGLE_OAUTH_RETURN_URL=https://your-frontend-domain.example/app/account/gmail
```

Use HTTPS for all public callback URLs. Set up a reverse proxy such as Caddy or Nginx for TLS, or use a hosting platform that provides TLS.

The Dockerfile installs:

- Python 3.14 slim runtime
- Playwright/Patchright Chromium and system dependencies
- FFmpeg for Instagram video normalization
- Tesseract OCR
- Python dependencies from `requirements.txt`

The frontend image in `frontend/Dockerfile` uses Node 22 to run `npm ci` and
`npm run build`, then copies the generated `dist/` directory into Nginx. The
frontend Nginx configuration provides React Router fallback behavior and
proxies `/api/` and `/uploads/` to the `api` Compose service.

The image runs as the non-root `appuser`. The default image command starts the API, Worker, and Beat through `start.sh`. Compose overrides that command so each process runs in its own container during local development.

## 11. Stop, restart, and reset commands

Stop containers without deleting data:

```powershell
docker compose stop
```

Start them again:

```powershell
docker compose start
```

Rebuild after source or Dockerfile changes:

```powershell
docker compose up -d --build
```

View all logs:

```powershell
docker compose logs -f
```

Stop and remove containers but keep named volumes:

```powershell
docker compose down
```

Stop and remove containers plus all database and Redis data:

```powershell
docker compose down -v
```

The final command is destructive. It deletes the local PostgreSQL database and Redis data.

## 12. Troubleshooting

### API exits immediately

Check:

```powershell
docker compose logs api
```

Usually this means `DATABASE_URL`, `REDIS_URL`, or `CREDENTIAL_ENCRYPTION_KEY` is missing or invalid.

### PostgreSQL connection refused

Inside Compose, use:

```text
postgresql+asyncpg://linkeflow:linkeflow_password@postgres:5432/linkeasy
```

Do not use `localhost` in the API container.

### Redis connection refused

Inside Compose, use:

```text
redis://redis:6379/0
```

Do not use `localhost` in the Worker or Beat containers.

### Frontend cannot call the API

Confirm:

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

Confirm the backend CORS setting includes:

```env
BACKEND_CORS_ORIGINS=http://localhost:5173
```

Restart the frontend after changing its `.env` file.

### OAuth redirect mismatch

Compare the callback URL in the provider console with the environment variable character by character. The callback is an API route, not the frontend route.

### WhatsApp or LinkedIn sessions disappear

Mount a persistent profile volume at `/app/profiles`. Without it, browser sessions are ephemeral and must be connected again after a container replacement.

### TikTok rejects localhost

TikTok requires a public HTTPS redirect URI. Use a tunnel during local development or a deployed backend URL.

## 13. Safe handoff checklist

Before giving the code to another person:

- Include `Dockerfile`, `docker-compose.yml`, `start.sh`, `requirements.txt`, and this guide.
- Include an `.env.example` template, but never include `.env`.
- Remove OAuth client secrets, API keys, access tokens, refresh tokens, and browser profiles.
- Tell the recipient to generate a new `JWT_SECRET` and `CREDENTIAL_ENCRYPTION_KEY`.
- Tell the recipient to create their own Google, Meta, TikTok, and AI provider credentials.
- Explain that database volumes contain private user data and must not be shared publicly.
- Explain that `docker compose down -v` deletes local database data.
- Use a separate production database and Redis instance rather than copying development volumes.

## 14. Quick-start summary

```powershell
# 1. Clone and enter the project
git clone <repository-url>
cd linkdin_automation

# 2. Create .env and fill required secrets
copy .env.example .env

# 3. Pull official database images
docker compose pull postgres redis

# 4. Build and start API, worker, beat, PostgreSQL, and Redis
docker compose up -d --build

# 5. Check services
docker compose ps

# 6. In another terminal, run the frontend
cd frontend
npm install
copy .env.example .env
npm run dev

# 7. Open the application
start http://localhost:5173
```

The exact root `.env.example` filename may differ in a checkout. If it is not present, create `.env` manually using the variables in section 4 and the settings defined in `core/config.py`.

## 15. Current Docker Hub confirmation

The current Compose configuration explicitly uses:

```text
postgres:16-alpine
redis:7-alpine
```

Therefore PostgreSQL and Redis are downloaded from Docker Hub automatically by Docker Compose when they are not already present locally. They are not installed by `pip`, bundled inside the Python application image, or downloaded by the FastAPI code.

The application image itself is built locally from the repository `Dockerfile`.
