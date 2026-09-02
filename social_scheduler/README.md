# Social Scheduler - Backend

A FastAPI backend for scheduling and publishing social media posts to YouTube Shorts, Instagram Reels, and TikTok.

## Tech Stack

- **Backend**: FastAPI (Python)
- **Database**: PostgreSQL with SQLAlchemy (Async)
- **Task Queue**: Celery with Redis
- **Platform APIs**: YouTube Data API, Instagram Graph API, TikTok Content Posting API

## Project Structure

```
social_scheduler/
├── api/              # FastAPI API routes
├── models/           # SQLAlchemy database models
├── schemas/          # Pydantic schemas for request/response
├── services/         # Platform integration services
│   ├── youtube.py    # YouTube Shorts integration
│   ├── instagram.py  # Instagram Reels integration
│   └── tiktok.py     # TikTok integration
├── tasks/            # Celery tasks for scheduling
│   ├── celery_app.py # Celery configuration
│   └── scheduler.py  # Post publishing logic
├── core/             # Core configuration and database
│   ├── config.py     # Settings and environment variables
│   └── database.py   # Database connection
└── main.py           # FastAPI application entry point
```

## Setup Instructions

### 1. Install Dependencies

```bash
cd social_scheduler
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file in the `social_scheduler` directory:

```env
# Database
DATABASE_URL="postgresql+asyncpg://postgres:password@localhost:5432/nexusflow_dev"

# Redis
REDIS_URL="redis://localhost:6379/0"

# App
APP_URL="http://localhost:3000"
CORS_ORIGINS="http://localhost:3000,http://localhost:3001"

# YouTube
YOUTUBE_CLIENT_ID="your_client_id"
YOUTUBE_CLIENT_SECRET="your_client_secret"
YOUTUBE_REDIRECT_URI="http://localhost:8000/api/platforms/youtube/callback"

# Instagram
INSTAGRAM_APP_ID="your_app_id"
INSTAGRAM_APP_SECRET="your_app_secret"
INSTAGRAM_REDIRECT_URI="http://localhost:8000/api/platforms/instagram/callback"

# TikTok
TIKTOK_CLIENT_KEY="your_client_key"
TIKTOK_CLIENT_SECRET="your_client_secret"
TIKTOK_REDIRECT_URI="http://localhost:8000/api/platforms/tiktok/callback"

# File Upload
UPLOAD_DIR="public/uploads"
MAX_UPLOAD_SIZE=524288000
```

### 3. Initialize Database

The database tables will be created automatically on startup. To run migrations manually:

```python
from core.database import init_db
import asyncio

asyncio.run(init_db())
```

### 4. Run the FastAPI Server

```bash
python main.py
```

The API will be available at `http://localhost:8000`

### 5. Run Celery Worker

```bash
celery -A tasks.celery_app worker --loglevel=info --concurrency=2
```

### 6. Run Celery Beat (for scheduled tasks)

```bash
celery -A tasks.celery_app beat --loglevel=info
```

## API Endpoints

### Posts

- `GET /api/posts` - Get all posts
- `GET /api/posts/{id}` - Get a specific post
- `POST /api/posts` - Create a new post
- `PUT /api/posts/{id}` - Update a post
- `DELETE /api/posts/{id}` - Delete a post

### Upload

- `POST /api/upload` - Upload a video file

### Platform Connections

- `GET /api/platforms/status` - Get connected platforms status
- `GET /api/platforms` - Get all platform connections
- `POST /api/platforms` - Create a platform connection
- `DELETE /api/platforms/{platform}` - Delete a platform connection

### Stats

- `GET /api/stats` - Get dashboard statistics

## Platform Setup

### YouTube Shorts

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable "YouTube Data API v3"
4. Create OAuth 2.0 credentials (Web Application)
5. Add `http://localhost:8000/api/platforms/youtube/callback` to Authorized Redirect URIs
6. Add credentials to `.env`

### Instagram Reels

1. Go to [Meta for Developers](https://developers.facebook.com/)
2. Create a new app (Business type)
3. Add Instagram Graph API product
4. Add `http://localhost:8000/api/platforms/instagram/callback` to redirect URIs
5. Add credentials to `.env`
6. **Important**: Instagram requires videos to be at a publicly accessible URL
7. For local testing, use ngrok: `ngrok http 8000`
8. Update `APP_URL` in `.env` to your ngrok URL

### TikTok

1. Go to [TikTok for Developers](https://developers.tiktok.com/)
2. Create an app
3. Request "Content Posting API" access (requires manual review)
4. Add `http://localhost:8000/api/platforms/tiktok/callback` to redirect URIs
5. Add credentials to `.env`

## Video Requirements

| Platform | Max Length | Max Size | Format | Aspect Ratio |
|----------|-----------|----------|--------|--------------|
| YouTube Shorts | 60 seconds | 128 GB | MP4, MOV | 9:16 (vertical) |
| Instagram Reels | 90 seconds | 650 MB | MP4, MOV | 9:16 (vertical) |
| TikTok | 10 minutes | 287 MB | MP4, MOV | 9:16 (vertical) |

**Recommended**: 9:16 vertical MP4, ≤60 seconds, ≤500MB for best compatibility.

## Frontend Integration

The frontend is located in the existing React app at `frontend/src/pages/social-scheduler/`.

### Frontend Structure

```
frontend/src/
├── api/
│   └── socialScheduler.js    # API client for social scheduler
└── pages/
    └── social-scheduler/
        └── Dashboard.jsx      # Main dashboard component
```

### Frontend Setup

1. The API client is configured to connect to `http://localhost:8000`
2. Add the social scheduler route to your React Router
3. Navigate to `/app/social-scheduler` to access the dashboard

## Troubleshooting

### Database Connection Issues

- Ensure PostgreSQL is running
- Check `DATABASE_URL` in `.env` is correct
- Verify database credentials

### Celery Worker Not Processing Tasks

- Ensure Redis is running
- Check `REDIS_URL` in `.env` is correct
- Verify Celery worker is running with correct broker URL

### Platform OAuth Errors

- Verify redirect URIs match in platform developer console
- Check API credentials are correct
- Ensure required scopes are granted

### Instagram Video Upload Failures

- Ensure video URL is publicly accessible
- Use ngrok for local testing
- Check `APP_URL` is set correctly

## Development

### Running in Development Mode

```bash
# Terminal 1 - FastAPI server
python main.py

# Terminal 2 - Celery worker
celery -A tasks.celery_app worker --loglevel=info

# Terminal 3 - Celery beat (optional)
celery -A tasks.celery_app beat --loglevel=info

# Terminal 4 - Frontend (from frontend directory)
npm start
```

### Testing

```bash
# Run FastAPI tests
pytest

# Run with coverage
pytest --cov=.
```

## Deployment

### Production Considerations

1. Use a production database (PostgreSQL)
2. Use a production Redis instance
3. Set proper CORS origins
4. Use environment-specific configuration
5. Enable HTTPS for OAuth callbacks
6. Use a process manager (systemd, supervisor) for Celery workers
7. Monitor Celery worker logs
8. Set up proper logging and monitoring

### Docker Deployment

A Dockerfile can be created for containerized deployment:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

## License

This project is part of the LinkeFlow automation suite.
