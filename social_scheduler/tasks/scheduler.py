import os
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from celery import shared_task
from tasks.celery_app import celery_app
from core.database import AsyncSessionLocal
from models import Post, PostResult, PlatformConnection
from services.youtube import YouTubeService
from services.instagram import InstagramService
from services.tiktok import TikTokService


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@celery_app.task
def process_due_posts():
    """Check for and process posts that are due for publishing."""
    import asyncio
    
    async def _process():
        async for db in get_db():
            now = datetime.utcnow()
            
            # Find all pending posts that are due
            result = await db.execute(
                select(Post).where(
                    Post.status == "pending",
                    Post.scheduled_at <= now
                )
            )
            due_posts = result.scalars().all()
            
            if not due_posts:
                return
            
            print(f"[Scheduler] Found {len(due_posts)} post(s) to publish")
            
            for post in due_posts:
                await publish_post(db, post)
    
    asyncio.run(_process())


async def publish_post(db: AsyncSession, post: Post):
    """Publish a post to its configured platforms."""
    import json
    
    platforms = json.loads(post.platforms) if isinstance(post.platforms, str) else post.platforms
    print(f"[Scheduler] Publishing post ID: {post.id} to platforms: {platforms}")
    
    # Resolve video path
    video_path = os.path.join(os.getcwd(), post.video_path)
    print(f"[Scheduler][{post.id}] Resolved video path: {video_path}")
    
    # Mark as posting
    post.status = "posting"
    await db.commit()
    print(f"[Scheduler][{post.id}] Status changed to posting")
    
    # Create result records for each platform
    for platform in platforms:
        existing = await db.execute(
            select(PostResult).where(
                PostResult.post_id == post.id,
                PostResult.platform == platform
            )
        )
        if not existing.scalar_one_or_none():
            db.add(PostResult(
                post_id=post.id,
                platform=platform,
                status="pending"
            ))
            await db.commit()
            print(f"[Scheduler][{post.id}] Created {platform} job result record")
    
    all_success = True
    caption = post.caption + (f"\n\n{post.hashtags}" if post.hashtags else "")
    
    # Get platform connections
    connections_result = await db.execute(select(PlatformConnection))
    connections = {c.platform: c for c in connections_result.scalars().all()}
    
    for platform in platforms:
        try:
            connection = connections.get(platform)
            
            if not connection:
                error = f"Account for {platform} is not connected. Go to Settings and connect the account."
                print(f"[Scheduler][{post.id}] {error}")
                await update_result(db, post.id, platform, "failed", "", "", error)
                all_success = False
                continue
            
            print(f"[Scheduler][{post.id}] Found {platform} connection")
            
            platform_id = ""
            platform_url = ""
            
            if platform == "youtube":
                yt_service = YouTubeService()
                yt_title = post.youtube_title or post.title
                yt_desc = caption
                
                result = await yt_service.upload_short(
                    video_path=video_path,
                    title=yt_title,
                    description=yt_desc,
                    access_token=connection.access_token,
                    refresh_token=connection.refresh_token or None
                )
                platform_id = result["video_id"]
                platform_url = result["video_url"]
                
                # Update tokens if refreshed
                # (Token refresh callback would be implemented here)
                
            elif platform == "instagram":
                ig_service = InstagramService()
                ig_caption = post.instagram_caption or caption
                public_video_url = f"{os.getenv('APP_URL', 'http://localhost:3000')}{post.video_url}"
                
                extra_data = json.loads(connection.extra_data) if connection.extra_data else {}
                result = await ig_service.publish_reel(
                    ig_user_id=connection.account_id,
                    video_url=public_video_url,
                    caption=ig_caption,
                    access_token=connection.access_token
                )
                platform_id = result["media_id"]
                platform_url = result["post_url"]
                
            elif platform == "tiktok":
                tt_service = TikTokService()
                tt_caption = post.tiktok_caption or caption
                
                result = await tt_service.upload_video(
                    video_path=video_path,
                    caption=tt_caption,
                    access_token=connection.access_token
                )
                platform_id = result["publish_id"]
                platform_url = result["video_url"]
            
            await update_result(db, post.id, platform, "posted", platform_id, platform_url, "")
            print(f"[Scheduler][{post.id}] ✅ Successfully posted to {platform}: {platform_url}")
            
        except Exception as e:
            error_message = str(e)
            print(f"[Scheduler][{post.id}] ❌ Failed to post to {platform}. Error: {error_message}")
            await update_result(db, post.id, platform, "failed", "", "", error_message)
            all_success = False
    
    # Update post status
    post.status = "posted" if all_success else "failed"
    await db.commit()
    print(f"[Scheduler][{post.id}] Job complete with status: {post.status}")


async def update_result(
    db: AsyncSession,
    post_id: str,
    platform: str,
    status: str,
    platform_id: str,
    platform_url: str,
    error: str
):
    """Update or create a post result record."""
    result = await db.execute(
        select(PostResult).where(
            PostResult.post_id == post_id,
            PostResult.platform == platform
        )
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        existing.status = status
        existing.platform_id = platform_id
        existing.platform_url = platform_url
        existing.error = error
        if status == "posted":
            existing.posted_at = datetime.utcnow()
    else:
        db.add(PostResult(
            post_id=post_id,
            platform=platform,
            status=status,
            platform_id=platform_id,
            platform_url=platform_url,
            error=error,
            posted_at=datetime.utcnow() if status == "posted" else None
        ))
    
    await db.commit()
