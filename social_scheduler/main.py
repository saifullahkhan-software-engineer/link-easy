from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta
from typing import List
import os
import shutil
import uuid

from core.config import settings
from core.database import get_db, init_db, Base
from models import Post, PostResult, PlatformConnection
from schemas import (
    PostCreate, PostUpdate, PostResponse, PostResult as PostResultSchema,
    PlatformConnectionCreate, PlatformConnectionResponse,
    PlatformAuthUrlResponse, PlatformTokenExchange, StatsResponse
)

app = FastAPI(title="Social Scheduler API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    await init_db()
    # Ensure upload directory exists
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)


@app.get("/")
async def root():
    return {"message": "Social Scheduler API is running"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


# Posts endpoints
@app.get("/api/posts", response_model=List[PostResponse])
async def get_posts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Post).order_by(Post.created_at.desc()))
    posts = result.scalars().all()
    return posts


@app.get("/api/posts/{post_id}", response_model=PostResponse)
async def get_post(post_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@app.post("/api/posts", response_model=PostResponse)
async def create_post(post: PostCreate, db: AsyncSession = Depends(get_db)):
    db_post = Post(**post.dict())
    db.add(db_post)
    await db.commit()
    await db.refresh(db_post)
    return db_post


@app.put("/api/posts/{post_id}", response_model=PostResponse)
async def update_post(post_id: str, post_update: PostUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Post).where(Post.id == post_id))
    db_post = result.scalar_one_or_none()
    if not db_post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    update_data = post_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_post, field, value)
    
    await db.commit()
    await db.refresh(db_post)
    return db_post


@app.delete("/api/posts/{post_id}")
async def delete_post(post_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Post).where(Post.id == post_id))
    db_post = result.scalar_one_or_none()
    if not db_post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    await db.delete(db_post)
    await db.commit()
    return {"message": "Post deleted successfully"}


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    # Generate unique filename
    file_extension = file.filename.split(".")[-1]
    unique_filename = f"{uuid.uuid4()}.{file_extension}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)
    
    # Save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return {
        "video_path": file_path,
        "video_url": f"/uploads/{unique_filename}"
    }


# Platform connections endpoints
@app.get("/api/platforms/status")
async def get_platform_status(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PlatformConnection))
    connections = result.scalars().all()
    connected = [c.platform for c in connections]
    return {"connected": connected}


@app.get("/api/platforms", response_model=List[PlatformConnectionResponse])
async def get_platform_connections(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PlatformConnection))
    connections = result.scalars().all()
    return connections


@app.post("/api/platforms", response_model=PlatformConnectionResponse)
async def create_platform_connection(
    connection: PlatformConnectionCreate,
    db: AsyncSession = Depends(get_db)
):
    # Check if connection already exists
    result = await db.execute(
        select(PlatformConnection).where(PlatformConnection.platform == connection.platform)
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        # Update existing
        for field, value in connection.dict().items():
            setattr(existing, field, value)
        await db.commit()
        await db.refresh(existing)
        return existing
    
    # Create new
    db_connection = PlatformConnection(**connection.dict())
    db.add(db_connection)
    await db.commit()
    await db.refresh(db_connection)
    return db_connection


@app.delete("/api/platforms/{platform}")
async def delete_platform_connection(platform: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PlatformConnection).where(PlatformConnection.platform == platform)
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Platform connection not found")
    
    await db.delete(connection)
    await db.commit()
    return {"message": "Platform connection deleted"}


# Stats endpoint
@app.get("/api/stats", response_model=StatsResponse)
async def get_stats(db: AsyncSession = Depends(get_db)):
    now = datetime.utcnow()
    week_from_now = now + timedelta(days=7)
    
    # Get all posts
    result = await db.execute(select(Post))
    posts = result.scalars().all()
    
    # Calculate stats
    scheduled_this_week = sum(
        1 for p in posts 
        if p.status == "pending" and p.scheduled_at <= week_from_now
    )
    total_published = sum(1 for p in posts if p.status == "posted")
    total_failed = sum(1 for p in posts if p.status == "failed")
    
    # Next post
    upcoming = [
        p for p in posts 
        if p.status == "pending" and p.scheduled_at > now
    ]
    upcoming.sort(key=lambda x: x.scheduled_at)
    next_post_in = None
    if upcoming:
        delta = upcoming[0].scheduled_at - now
        next_post_in = f"in {delta.days} days {delta.seconds // 3600} hours"
    
    # Connected platforms
    conn_result = await db.execute(select(PlatformConnection))
    connections = conn_result.scalars().all()
    connected_platforms = [c.platform for c in connections]
    
    return StatsResponse(
        scheduled_this_week=scheduled_this_week,
        total_published=total_published,
        total_failed=total_failed,
        next_post_in=next_post_in,
        connected_platforms=connected_platforms
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
