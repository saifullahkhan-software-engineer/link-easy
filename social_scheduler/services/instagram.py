import asyncio
import aiohttp
from typing import Dict, Any, Optional
from core.config import settings


class InstagramService:
    """Service for Instagram Reels publishing."""
    
    GRAPH_API = "https://graph.facebook.com/v18.0"
    
    def __init__(self):
        self.app_id = settings.INSTAGRAM_APP_ID
        self.app_secret = settings.INSTAGRAM_APP_SECRET
        self.redirect_uri = settings.INSTAGRAM_REDIRECT_URI
    
    def get_auth_url(self) -> str:
        """Generate OAuth authorization URL."""
        params = {
            "client_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "scope": "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement",
            "response_type": "code",
            "state": "instagram_auth"
        }
        
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"https://www.facebook.com/v18.0/dialog/oauth?{query_string}"
    
    async def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access token."""
        params = {
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "redirect_uri": self.redirect_uri,
            "code": code
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.GRAPH_API}/oauth/access_token",
                data=params
            ) as response:
                data = await response.json()
                
                if "error" in data:
                    raise Exception(f"Instagram token exchange failed: {data['error']['message']}")
                
                return {
                    "access_token": data.get("access_token"),
                    "token_type": data.get("token_type"),
                    "expires_in": data.get("expires_in")
                }
    
    async def get_long_lived_token(self, short_token: str) -> Dict[str, Any]:
        """Exchange short-lived token for long-lived token."""
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "fb_exchange_token": short_token
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.GRAPH_API}/oauth/access_token",
                params=params
            ) as response:
                data = await response.json()
                
                if "error" in data:
                    raise Exception(f"Instagram long-lived token failed: {data['error']['message']}")
                
                return {
                    "access_token": data.get("access_token"),
                    "expires_in": data.get("expires_in")
                }
    
    async def get_instagram_account_info(self, access_token: str) -> Dict[str, str]:
        """Get Instagram Business account information."""
        async with aiohttp.ClientSession() as session:
            # Get Facebook pages
            async with session.get(
                f"{self.GRAPH_API}/me/accounts",
                params={"access_token": access_token}
            ) as response:
                pages_data = await response.json()
                
                if "error" in pages_data:
                    raise Exception(f"Failed to get Facebook pages: {pages_data['error']['message']}")
                
                pages = pages_data.get("data", [])
                if not pages:
                    raise Exception("No Facebook Page found. You need a Facebook Page linked to your Instagram Business account.")
                
                page = pages[0]
            
            # Get Instagram account linked to the page
            async with session.get(
                f"{self.GRAPH_API}/{page['id']}",
                params={
                    "fields": "instagram_business_account",
                    "access_token": access_token
                }
            ) as response:
                ig_data = await response.json()
                
                if "error" in ig_data:
                    raise Exception(f"Failed to get Instagram account: {ig_data['error']['message']}")
                
                ig_account = ig_data.get("instagram_business_account")
                if not ig_account:
                    raise Exception("No Instagram Business/Creator account linked to your Facebook Page.")
                
                ig_account_id = ig_account["id"]
            
            # Get account name
            async with session.get(
                f"{self.GRAPH_API}/{ig_account_id}",
                params={
                    "fields": "username",
                    "access_token": access_token
                }
            ) as response:
                name_data = await response.json()
                
                if "error" in name_data:
                    raise Exception(f"Failed to get Instagram username: {name_data['error']['message']}")
                
                username = name_data.get("username", "Instagram Account")
            
            return {
                "account_id": ig_account_id,
                "account_name": username,
                "page_id": page["id"]
            }
    
    async def publish_reel(
        self,
        ig_user_id: str,
        video_url: str,
        caption: str,
        access_token: str
    ) -> Dict[str, str]:
        """Publish a video as Instagram Reel."""
        
        # Step 1: Create media container
        container_data = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption[:2200],  # Max 2200 chars
            "share_to_feed": "true",
            "access_token": access_token
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.GRAPH_API}/{ig_user_id}/media",
                json=container_data
            ) as response:
                container_response = await response.json()
                
                if "error" in container_response:
                    error_msg = container_response["error"].get("message", "Unknown error")
                    raise Exception(f"Instagram container creation failed: {error_msg}")
                
                creation_id = container_response.get("id")
                if not creation_id:
                    raise Exception("Failed to create Instagram media container")
            
            # Step 2: Wait for video processing
            await self._wait_for_processing(ig_user_id, creation_id, access_token)
            
            # Step 3: Publish the container
            publish_data = {
                "creation_id": creation_id,
                "access_token": access_token
            }
            
            async with session.post(
                f"{self.GRAPH_API}/{ig_user_id}/media_publish",
                json=publish_data
            ) as response:
                publish_response = await response.json()
                
                if "error" in publish_response:
                    error_msg = publish_response["error"].get("message", "Unknown error")
                    raise Exception(f"Instagram publish failed: {error_msg}")
                
                media_id = publish_response.get("id", "")
            
            return {
                "media_id": media_id,
                "post_url": f"https://www.instagram.com/reel/{media_id}/"
            }
    
    async def _wait_for_processing(
        self,
        ig_user_id: str,
        creation_id: str,
        access_token: str,
        max_attempts: int = 20
    ) -> None:
        """Wait for Instagram video processing to complete."""
        
        async with aiohttp.ClientSession() as session:
            for attempt in range(max_attempts):
                async with session.get(
                    f"{self.GRAPH_API}/{creation_id}",
                    params={
                        "fields": "status_code,status",
                        "access_token": access_token
                    }
                ) as response:
                    data = await response.json()
                    
                    status_code = data.get("status_code")
                    
                    if status_code == "FINISHED":
                        return
                    elif status_code == "ERROR":
                        status = data.get("status", "Unknown error")
                        raise Exception(f"Instagram video processing failed: {status}")
                    
                    # Wait 15 seconds between polls
                    await asyncio.sleep(15)
            
            raise Exception("Instagram video processing timed out")
