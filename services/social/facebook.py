"""Facebook Page video publishing through the Meta Graph API."""
from typing import Any, Dict, Optional
from urllib.parse import urlencode
import aiohttp

from core.config import settings


class FacebookService:
    GRAPH_API = "https://graph.facebook.com/v20.0"
    SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,publish_video"

    def __init__(self):
        self.app_id = settings.FACEBOOK_APP_ID
        self.app_secret = settings.FACEBOOK_APP_SECRET
        self.redirect_uri = settings.FACEBOOK_REDIRECT_URI

    def get_auth_url(self, state: str) -> str:
        return "https://www.facebook.com/v20.0/dialog/oauth?" + urlencode({
            "client_id": self.app_id, "redirect_uri": self.redirect_uri,
            "scope": self.SCOPES, "response_type": "code", "state": state,
        })

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        params = {"client_id": self.app_id, "client_secret": self.app_secret,
                  "redirect_uri": self.redirect_uri, "code": code}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.GRAPH_API}/oauth/access_token", params=params) as r:
                data = await r.json()
            if "error" in data: raise ValueError(data["error"].get("message", "Facebook OAuth failed"))
            user_token = data.get("access_token")
            async with session.get(f"{self.GRAPH_API}/me/accounts", params={
                "fields": "id,name,access_token", "access_token": user_token}) as r:
                pages = await r.json()
        if pages.get("error"): raise ValueError(pages["error"].get("message", "Could not read Facebook Pages"))
        page = (pages.get("data") or [None])[0]
        if not page or not page.get("access_token"):
            raise ValueError("No Facebook Page was found for this account")
        # Store the selected (first) Page token; it is scoped to that Page.
        return {"access_token": page["access_token"], "refresh_token": None,
                "expires_in": data.get("expires_in")}

    async def refresh_access_token(self, refresh_token: Optional[str], current_access_token: Optional[str] = None):
        raise ValueError("Facebook Page access expired. Reconnect Facebook.")

    async def get_account_info(self, access_token: str) -> Dict[str, str]:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.GRAPH_API}/me", params={"fields": "id,name", "access_token": access_token}) as r:
                data = await r.json()
        if data.get("error"): raise ValueError(data["error"].get("message", "Facebook account lookup failed"))
        return {"account_id": data.get("id", ""), "account_name": data.get("name", "")}

    async def upload_video(self, video_path: str, description: str, access_token: str):
        form = aiohttp.FormData()
        form.add_field("source", open(video_path, "rb"), filename="video.mp4", content_type="video/mp4")
        form.add_field("description", description)
        form.add_field("access_token", access_token)
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.GRAPH_API}/me/videos", data=form) as r:
                data = await r.json()
        if data.get("error"): raise ValueError(data["error"].get("message", "Facebook upload failed"))
        video_id = data.get("id") or data.get("video_id")
        return {"video_id": video_id, "video_url": f"https://www.facebook.com/{video_id}"}
