"""Platform integrations for the social post scheduler.

One service per platform, each exposing the same surface:

    get_auth_url(state)                 -> str
    exchange_code(code)                 -> {"access_token", "refresh_token", "expires_in"}
    refresh_access_token(refresh_token) -> same shape (None where unsupported)
    get_account_info(access_token)      -> {"account_id", "account_name", ...}
    publish(...)                        -> {"platform_id", "platform_url"}

Ported from social_scheduler/services/. The upload/publish method names of
the originals (upload_short / publish_reel / upload_video) are kept as-is.
"""
from .instagram import InstagramService
from .tiktok import TikTokService
from .youtube import YouTubeService
from .facebook import FacebookService

SERVICES = {
    "youtube": YouTubeService,
    "instagram": InstagramService,
    "tiktok": TikTokService,
    "facebook": FacebookService,
}


def get_service(platform: str):
    try:
        return SERVICES[platform]()
    except KeyError:
        raise ValueError(f"Unsupported platform: {platform}") from None


__all__ = ["YouTubeService", "InstagramService", "TikTokService", "SERVICES", "get_service"]
