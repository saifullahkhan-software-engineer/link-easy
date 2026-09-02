import os
import asyncio
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from typing import Optional, Dict, Any
from core.config import settings


class YouTubeService:
    """Service for YouTube Shorts upload and management."""
    
    SCOPES = [
        'https://www.googleapis.com/auth/youtube.upload',
        'https://www.googleapis.com/auth/youtube.readonly',
        'https://www.googleapis.com/auth/userinfo.profile',
    ]
    
    def __init__(self):
        self.client_id = settings.YOUTUBE_CLIENT_ID
        self.client_secret = settings.YOUTUBE_CLIENT_SECRET
        self.redirect_uri = settings.YOUTUBE_REDIRECT_URI
    
    def get_auth_url(self) -> str:
        """Generate OAuth authorization URL."""
        from google_auth_oauthlib.flow import InstalledAppFlow
        
        flow = InstalledAppFlow.from_client_config(
            client_config={
                "installed": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [self.redirect_uri]
                }
            },
            scopes=self.SCOPES
        )
        
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            include_granted_scopes='true'
        )
        
        return auth_url
    
    async def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for tokens."""
        from google_auth_oauthlib.flow import InstalledAppFlow
        
        flow = InstalledAppFlow.from_client_config(
            client_config={
                "installed": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [self.redirect_uri]
                }
            },
            scopes=self.SCOPES
        )
        
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        return {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None
        }
    
    async def get_channel_info(self, access_token: str, refresh_token: Optional[str] = None) -> Dict[str, str]:
        """Get YouTube channel information."""
        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret
        )
        
        youtube = build('youtube', 'v3', credentials=credentials)
        
        try:
            response = youtube.channels().list(
                part='snippet',
                mine=True
            ).execute()
            
            channel = response.get('items', [{}])[0]
            return {
                "account_id": channel.get('id', ''),
                "account_name": channel.get('snippet', {}).get('title', 'YouTube Channel')
            }
        except HttpError as e:
            raise Exception(f"YouTube API error: {e}")
    
    async def upload_short(
        self,
        video_path: str,
        title: str,
        description: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        on_tokens_callback = None
    ) -> Dict[str, str]:
        """Upload a video as YouTube Short."""
        
        # Validate video file
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        file_size = os.path.getsize(video_path)
        if file_size == 0:
            raise ValueError("Video file is empty")
        
        # Create credentials
        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret
        )
        
        # Set up token refresh callback
        if on_tokens_callback:
            async def token_callback(token_info):
                await on_tokens_callback({
                    "access_token": token_info.get("access_token"),
                    "refresh_token": token_info.get("refresh_token"),
                    "expiry_date": token_info.get("expiry_date")
                })
            credentials.token = access_token
            credentials.refresh_token = refresh_token
        
        youtube = build('youtube', 'v3', credentials=credentials)
        
        # Ensure #Shorts is in title and description
        short_title = title if '#Shorts' in title else f"{title} #Shorts"
        short_description = f"{description}\n\n#Shorts"
        
        try:
            # Upload video
            body = {
                'snippet': {
                    'title': short_title[:100],  # Max 100 chars
                    'description': short_description[:5000],
                    'categoryId': '22',  # People & Blogs
                    'tags': ['Shorts', 'Short'],
                    'defaultLanguage': 'en',
                },
                'status': {
                    'privacyStatus': 'public',
                    'selfDeclaredMadeForKids': False,
                },
            }
            
            media = MediaFileUpload(
                video_path,
                mimetype='video/mp4',
                resumable=True
            )
            
            response = youtube.videos().insert(
                part='snippet,status',
                body=body,
                media_body=media
            ).execute()
            
            video_id = response.get('id', '')
            if not video_id:
                raise Exception("YouTube API did not return a video ID")
            
            return {
                "video_id": video_id,
                "video_url": f"https://www.youtube.com/shorts/{video_id}"
            }
            
        except HttpError as e:
            error_detail = e.error_details[0] if e.error_details else {}
            reason = error_detail.get('reason', 'unknown')
            message = error_detail.get('message', str(e))
            
            # Provide actionable error messages
            if reason == 'youtubeSignupRequired':
                raise Exception("Create a YouTube channel for the connected Google account, then reconnect it.")
            elif reason == 'uploadLimitExceeded':
                raise Exception("The channel has reached its daily upload limit. Try again later.")
            elif reason == 'quotaExceeded' or reason == 'dailyLimitExceeded':
                raise Exception("The Google Cloud project has exhausted its YouTube API quota.")
            elif e.resp.status == 401:
                raise Exception("Reconnect YouTube to grant a fresh upload token.")
            elif e.resp.status == 403:
                raise Exception("Confirm YouTube Data API v3 is enabled and account owns a YouTube channel.")
            else:
                raise Exception(f"YouTube upload failed: {message}")
        
        except Exception as e:
            raise Exception(f"YouTube upload error: {str(e)}")
