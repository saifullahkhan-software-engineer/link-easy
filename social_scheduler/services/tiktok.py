import os
import asyncio
import aiohttp
from typing import Dict, Any, Optional
from core.config import settings


class TikTokService:
    """Service for TikTok video publishing."""
    
    TIKTOK_API = "https://open.tiktokapis.com/v2"
    
    def __init__(self):
        self.client_key = settings.TIKTOK_CLIENT_KEY
        self.client_secret = settings.TIKTOK_CLIENT_SECRET
        self.redirect_uri = settings.TIKTOK_REDIRECT_URI
    
    def get_auth_url(self) -> str:
        """Generate OAuth authorization URL."""
        params = {
            "client_key": self.client_key,
            "response_type": "code",
            "scope": "user.info.basic,video.publish,video.upload",
            "redirect_uri": self.redirect_uri,
            "state": f"tiktok_{asyncio.get_event_loop().time()}"
        }
        
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"https://www.tiktok.com/v2/auth/authorize/?{query_string}"
    
    async def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access token."""
        params = {
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.TIKTOK_API}/oauth/token/",
                data=params
            ) as response:
                data = await response.json()
                
                if "error" in data:
                    raise Exception(f"TikTok token exchange failed: {data['error'].get('message', 'Unknown error')}")
                
                return {
                    "access_token": data.get("access_token"),
                    "refresh_token": data.get("refresh_token"),
                    "expires_in": data.get("expires_in"),
                    "token_type": data.get("token_type")
                }
    
    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh access token using refresh token."""
        params = {
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.TIKTOK_API}/oauth/token/",
                data=params
            ) as response:
                data = await response.json()
                
                if "error" in data:
                    raise Exception(f"TikTok token refresh failed: {data['error'].get('message', 'Unknown error')}")
                
                return {
                    "access_token": data.get("access_token"),
                    "refresh_token": data.get("refresh_token"),
                    "expires_in": data.get("expires_in")
                }
    
    async def get_user_info(self, access_token: str) -> Dict[str, str]:
        """Get TikTok user information."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.TIKTOK_API}/user/info/",
                params={
                    "fields": "open_id,union_id,avatar_url,display_name"
                },
                headers={"Authorization": f"Bearer {access_token}"}
            ) as response:
                data = await response.json()
                
                if "error" in data:
                    raise Exception(f"TikTok user info failed: {data['error'].get('message', 'Unknown error')}")
                
                user_data = data.get("data", {}).get("user", {})
                return {
                    "account_id": user_data.get("open_id", ""),
                    "account_name": user_data.get("display_name", "TikTok Account")
                }
    
    async def upload_video(
        self,
        video_path: str,
        caption: str,
        access_token: str
    ) -> Dict[str, str]:
        """Upload a video to TikTok."""
        
        # Validate video file
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        file_size = os.path.getsize(video_path)
        
        async with aiohttp.ClientSession() as session:
            # Step 1: Initialize upload
            init_data = {
                "post_info": {
                    "title": caption[:2200],  # Max 2200 chars
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "video_cover_timestamp_ms": 1000
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": file_size,  # Single chunk for simplicity
                    "total_chunk_count": 1
                }
            }
            
            async with session.post(
                f"{self.TIKTOK_API}/post/publish/video/init/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8"
                },
                json=init_data
            ) as response:
                init_response = await response.json()
                
                error = init_response.get("error")
                if error and error.get("code") != "ok":
                    error_msg = error.get("message", "Unknown error")
                    raise Exception(f"TikTok init failed: {error_msg}")
                
                data = init_response.get("data", {})
                publish_id = data.get("publish_id")
                upload_url = data.get("upload_url")
                
                if not publish_id or not upload_url:
                    raise Exception("TikTok did not return upload URL")
            
            # Step 2: Upload the video file
            with open(video_path, "rb") as video_file:
                file_content = video_file.read()
            
            headers = {
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                "Content-Length": str(file_size)
            }
            
            async with session.put(upload_url, headers=headers, data=file_content) as response:
                if not response.ok:
                    error_text = await response.text()
                    raise Exception(f"TikTok file upload failed: {response.status} {error_text}")
            
            # Step 3: Poll for publish status
            video_url = await self._poll_publish_status(publish_id, access_token)
            
            return {
                "publish_id": publish_id,
                "video_url": video_url
            }
    
    async def _poll_publish_status(
        self,
        publish_id: str,
        access_token: str,
        max_attempts: int = 30
    ) -> str:
        """Poll for TikTok publish status."""
        
        async with aiohttp.ClientSession() as session:
            for attempt in range(max_attempts):
                await asyncio.sleep(10)  # Wait 10 seconds between polls
                
                async with session.post(
                    f"{self.TIKTOK_API}/post/publish/status/fetch/",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json; charset=UTF-8"
                    },
                    json={"publish_id": publish_id}
                ) as response:
                    data = await response.json()
                    
                    publish_data = data.get("data", {})
                    status = publish_data.get("status")
                    
                    if status == "PUBLISH_COMPLETE":
                        video_ids = publish_data.get("publicaly_available_post_id", [])
                        video_id = video_ids[0] if video_ids else publish_id
                        return f"https://www.tiktok.com/@me/video/{video_id}"
                    elif status == "FAILED":
                        fail_reason = publish_data.get("fail_reason", "Unknown")
                        raise Exception(f"TikTok publish failed: {fail_reason}")
            
            raise Exception("TikTok publish timed out")
