from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from api.dependencies import get_db, require_roles
from api.v1.auth import router as auth_router
from api.v1.linkedin import router as linkedin_router
from api.v1.users import router as users_router
from api.v1.campaigns import router as campaign_router
from api.v1.leads import router as leads_router

from api.v1.test_automation import router as test_automation_router
from core.config import settings
from database import init_db
from models.roles import UserRole
from api.dependencies import get_current_user
from models.user import User
from automation.session_manager import start_periodic_cleanup

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run database initializations on startup, start background tasks."""
    await init_db()
    cleanup_task = start_periodic_cleanup(interval_seconds=300, timeout_minutes=15)
    try:
        yield
    finally:
        cleanup_task.cancel()


app = FastAPI(title="LinkeFlow Authentication API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(linkedin_router)
app.include_router(campaign_router)
app.include_router(leads_router)
app.include_router(test_automation_router) 


@app.get("/")
async def root():
    return {"message": "LinkeFlow auth service is running"}


@app.get("/db-check")
async def db_check(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    """Authenticated endpoint to check database connectivity."""
    try:
        # Perform a simple query to check the connection
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "message": "Database connection is healthy."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection error",
        )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
