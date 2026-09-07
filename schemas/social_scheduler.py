"""
Social post scheduler — Pydantic schemas.
FILE: schemas/social_scheduler.py

Ported from social_scheduler/schemas/__init__.py. Changes on the way in:

* ``PostCreate`` no longer accepts ``video_path``. The worker opens that path
  and streams the file to YouTube/TikTok, so letting the client choose it was
  an arbitrary-file-read. Clients now pass the ``upload_id`` returned by the
  upload endpoint and the server resolves the path itself.
* Platform names and the status filter are validated against the enums in
  models/social_scheduler.py instead of free-form strings.
* Token-bearing fields never appear in any response schema; the connection
  response exposes account metadata only.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from models.social_scheduler import SocialContentKind, SocialMediaKind, SocialPlatform, SocialPostStatus

PLATFORM_VALUES = tuple(p.value for p in SocialPlatform)
CONTENT_KIND_VALUES = tuple(k.value for k in SocialContentKind)
MEDIA_KIND_VALUES = tuple(k.value for k in SocialMediaKind)
SCHEDULING_METHOD_VALUES = ("linkeasy", "native")
#: Playlists one Short may be filed into. A Short is one video; more than a
#: handful of collections is a mistake, not a feature.
MAX_PLAYLISTS_PER_POST = 10
#: Groups one Reel can be listed for *manual* sharing. Each row is a page the
#: user opens and posts into, so the cap is a usability limit, not a technical
#: one.
MAX_GROUPS_PER_POST = 25
PLATFORM_LABELS = {
    "youtube": "YouTube",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}


def _validate_platforms(values: list[str]) -> list[str]:
    if not values:
        raise ValueError("Select at least one platform")
    cleaned: list[str] = []
    for raw in values:
        value = str(raw).strip().lower()
        if value not in PLATFORM_VALUES:
            raise ValueError(f"Unknown platform '{raw}'. Choose from: {', '.join(PLATFORM_VALUES)}")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


def _validate_playlist_ids(values: list[str]) -> list[str]:
    """Clean a list of YouTube playlist ids.

    Playlist ids are opaque strings (``PL…``/``UU…``/``FL…`` in practice), so
    the only rules are: non-empty, de-duplicated (the picker cannot select one
    twice, but a hand-built request could), and few enough that publishing
    cannot fan out into an unreasonable number of API calls.
    """
    cleaned: list[str] = []
    for raw in values or []:
        value = str(raw).strip()
        if not value:
            continue
        if len(value) > 100:
            raise ValueError("A YouTube playlist id is too long to be valid")
        if value not in cleaned:
            cleaned.append(value)
    if len(cleaned) > MAX_PLAYLISTS_PER_POST:
        raise ValueError(f"Choose at most {MAX_PLAYLISTS_PER_POST} playlists")
    return cleaned


def _clean_platform_accounts(
    values: dict[str, Any], platforms: list[str]
) -> dict[str, list[str]]:
    """Normalise the upload editor's account picks ({platform: connection id}).

    Rules:
      * only keys for *selected* platforms survive (a stale pick for an
        unselected platform would be dead weight on the row);
      * ids are trimmed, non-empty and capped (they are row uuids, so anything
        long is a client bug, not an account);
      * de-duplicated by key (dicts already are, but hand-built JSON could
        carry a malformed shape through a proxy).
    Ownership of every id (does this user really control that account?) is
    checked in the route, not here — the schema sees no database.
    """
    cleaned: dict[str, list[str]] = {}
    selected = set(platforms or [])
    for raw_platform, raw_id in (values or {}).items():
        platform = str(raw_platform).strip().lower()
        if platform not in selected:
            continue
        raw_ids = raw_id if isinstance(raw_id, list) else [raw_id]
        connection_ids: list[str] = []
        for raw_connection_id in raw_ids:
            connection_id = str(raw_connection_id or "").strip()
            if not connection_id or len(connection_id) > 100:
                raise ValueError(f"Invalid account selection for {platform}")
            if connection_id not in connection_ids:
                connection_ids.append(connection_id)
        if not connection_ids:
            raise ValueError(f"Invalid account selection for {platform}")
        cleaned[platform] = connection_ids
    return cleaned


def _validate_facebook_groups(groups: list["FacebookGroup"]) -> list["FacebookGroup"]:
    """Clean the manual Facebook Group selection stored on a post.

    De-duplicated by URL (the same group twice is one checklist row) and
    capped: this is a checklist a human works through, not a fan-out list.
    """
    cleaned: list["FacebookGroup"] = []
    seen = set()
    for group in groups or []:
        url = (group.url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        cleaned.append(FacebookGroup(name=(group.name or "").strip() or url, url=url))
    if len(cleaned) > MAX_GROUPS_PER_POST:
        raise ValueError(f"Choose at most {MAX_GROUPS_PER_POST} groups")
    return cleaned


def _ensure_aware(value: datetime) -> datetime:
    # A bare "2026-09-10T18:00" from a <input type=datetime-local> is taken as
    # UTC; a value with an offset is normalised to UTC for comparisons.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ── Posts ─────────────────────────────────────────────────────────────────────


def _clean_group_url(value: str) -> str:
    """Strip and scheme-check a group link.

    These end up as ``<a href>`` targets in the UI, so only http(s) is
    accepted — a ``javascript:`` or ``data:`` URL stored here would be a stored
    XSS the moment a checklist renders it.
    """
    url = (value or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("A group link must start with http:// or https://")
    return url


class FacebookGroup(BaseModel):
    """One group a Reel should be shared to, by the user, after publishing.

    Meta removed the Groups API on 22 Apr 2024, so this is never an API target:
    it is a row in a checklist the user works through in their own browser.
    """

    name: str = Field(..., min_length=1, max_length=120)
    url: str = Field(..., min_length=1, max_length=500)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _clean_group_url(v)


class PostCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    caption: str = Field("", max_length=5000)
    hashtags: str = Field("", max_length=1000)
    # Returned by POST /upload — the server maps it back to the stored file.
    upload_id: str = Field(..., min_length=1, max_length=200)
    thumbnail: str = ""
    platforms: list[str]
    # A direct upload does not need a client-provided time; the API stamps it
    # when the post is committed. Scheduled posts must provide one.
    scheduled_at: Optional[datetime] = None
    scheduling_method: str = "linkeasy"
    youtube_title: str = Field("", max_length=100)
    instagram_caption: str = Field("", max_length=2200)
    tiktok_caption: str = Field("", max_length=2200)
    # Structured per-platform copy populated by the upload editor — typed by
    # hand, filled by the page's local parser, or extracted by
    # POST /parse-copy (services/ai/copy_parser.py). Example:
    # {"youtube": {"title": ...}}
    platform_copy: dict[str, dict[str, str]] = Field(default_factory=dict)
    # YouTube playlists the Short is added to once the upload succeeds.
    youtube_playlist_ids: list[str] = Field(default_factory=list)
    # Facebook Groups to share the Reel to *by hand* — Meta removed the Groups
    # API, so these become a post-publish checklist, never an API call.
    facebook_groups: list[FacebookGroup] = Field(default_factory=list)
    # Which accounts of each selected platform should publish this post
    # ({platform: [social_platform_connections.id]}). A scalar is accepted
    # for backwards compatibility with older upload editors.
    platform_accounts: dict[str, Any] = Field(default_factory=dict)
    youtube_playlists_by_account: dict[str, list[str]] = Field(default_factory=dict)
    publish_now: bool = False
    # ``shorts`` (default, existing behaviour) vs a regular feed ``post``.
    # The server still infers ``media_kind`` from the uploaded file.
    content_kind: str = Field(SocialContentKind.SHORTS.value, max_length=20)
    # UI selection, used for diagnostics only. The API never trusts this over
    # the server-generated upload extension.
    media_kind: str = Field(SocialMediaKind.VIDEO.value, max_length=20)

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v):
        return _validate_platforms(v)

    @model_validator(mode="after")
    def _normalize_platform_accounts(self) -> "PostCreate":
        self.platform_accounts = _clean_platform_accounts(
            self.platform_accounts, self.platforms
        )
        return self

    @field_validator("youtube_playlists_by_account")
    @classmethod
    def _playlists_by_account(cls, value):
        cleaned = {}
        for account_id, playlist_ids in (value or {}).items():
            account = str(account_id).strip()
            if not account or len(account) > 100:
                raise ValueError("Invalid YouTube account for playlist selection")
            cleaned[account] = _validate_playlist_ids(playlist_ids)
        return cleaned

    @field_validator("media_kind")
    @classmethod
    def _media_kind(cls, v):
        value = str(v).strip().lower()
        if value not in MEDIA_KIND_VALUES:
            raise ValueError(f"Unknown media kind '{v}'. Choose from: {', '.join(MEDIA_KIND_VALUES)}")
        return value

    @field_validator("scheduling_method")
    @classmethod
    def _scheduling_method(cls, v):
        value = str(v or "linkeasy").strip().lower()
        if value not in SCHEDULING_METHOD_VALUES:
            raise ValueError("scheduling_method must be native or linkeasy")
        return value

    @field_validator("youtube_playlist_ids")
    @classmethod
    def _playlists(cls, v):
        return _validate_playlist_ids(v)

    @field_validator("facebook_groups")
    @classmethod
    def _groups(cls, v):
        return _validate_facebook_groups(v)

    @field_validator("scheduled_at")
    @classmethod
    def _aware(cls, v):
        return None if v is None else _ensure_aware(v)

    @field_validator("title", "caption", "hashtags", "youtube_title", "instagram_caption", "tiktok_caption")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("content_kind")
    @classmethod
    def _content_kind(cls, v):
        value = (v or SocialContentKind.SHORTS.value).strip().lower()
        if value not in CONTENT_KIND_VALUES:
            raise ValueError(f"content_kind must be one of: {', '.join(CONTENT_KIND_VALUES)}")
        return value


class PostUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    caption: Optional[str] = Field(None, max_length=5000)
    hashtags: Optional[str] = Field(None, max_length=1000)
    platforms: Optional[list[str]] = None
    scheduled_at: Optional[datetime] = None
    # Only "pending" (re-schedule a failed/cancelled post) and "cancelled"
    # may be set by the client; the worker owns posting/posted/failed.
    status: Optional[str] = None
    youtube_title: Optional[str] = Field(None, max_length=100)
    instagram_caption: Optional[str] = Field(None, max_length=2200)
    tiktok_caption: Optional[str] = Field(None, max_length=2200)
    platform_copy: Optional[dict[str, dict[str, str]]] = None
    youtube_playlist_ids: Optional[list[str]] = None
    facebook_groups: Optional[list[FacebookGroup]] = None

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v):
        return None if v is None else _validate_platforms(v)

    @field_validator("youtube_playlist_ids")
    @classmethod
    def _playlists(cls, v):
        return None if v is None else _validate_playlist_ids(v)

    @field_validator("facebook_groups")
    @classmethod
    def _groups(cls, v):
        return None if v is None else _validate_facebook_groups(v)

    @field_validator("scheduled_at")
    @classmethod
    def _aware(cls, v):
        return None if v is None else _ensure_aware(v)

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v is None:
            return None
        value = str(v).strip().lower()
        allowed = (SocialPostStatus.PENDING.value, SocialPostStatus.CANCELLED.value)
        if value not in allowed:
            raise ValueError(f"status may only be set to {' or '.join(allowed)}")
        return value


class PostResultResponse(BaseModel):
    id: str
    platform: str
    status: str
    platform_id: str = ""
    platform_url: str = ""
    error: str = ""
    # Non-fatal detail on a *successful* publish, e.g. which playlists could
    # not be updated. Kept apart from ``error`` because the UI only shows
    # ``error`` for failed rows.
    note: str = ""
    posted_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PostResponse(BaseModel):
    id: str
    title: str
    caption: str
    hashtags: str
    video_url: str
    thumbnail: str
    platforms: list[str]
    content_kind: str = SocialContentKind.SHORTS.value
    media_kind: str = SocialMediaKind.VIDEO.value
    scheduled_at: datetime
    scheduling_method: str = "linkeasy"
    status: str
    youtube_title: str
    instagram_caption: str
    tiktok_caption: str
    platform_copy: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Platform → selected connection ids.
    platform_accounts: dict[str, Any] = Field(default_factory=dict)
    youtube_playlist_ids: list[str] = Field(default_factory=list)
    youtube_playlists_by_account: dict[str, list[str]] = Field(default_factory=dict)
    facebook_groups: list[FacebookGroup] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    results: list[PostResultResponse] = []

    model_config = {"from_attributes": True}


class PostDeleteResponse(BaseModel):
    message: str
    id: str


# ── Upload ────────────────────────────────────────────────────────────────────


# ── Upload / edit ─────────────────────────────────────────────────────────────
# ``POST /upload`` stores the raw clip and reports its duration (so the upload
# editor can draw a scrubber). ``POST /uploads/{id}/trim`` re-encodes the kept
# range in place and returns the same shape with the new size/duration.
# ``POST /uploads/{id}/thumbnail`` produces a frame-of-the-video or stored
# uploaded image and reports its public URL.


class UploadResponse(BaseModel):
    upload_id: str
    filename: str
    size_bytes: int
    content_type: str
    video_url: str
    # Seconds, when ffmpeg is available to read it (None otherwise). The trim
    # controls are hidden/disabled whenever this is missing.
    duration_seconds: Optional[float] = None


class TrimRequest(BaseModel):
    """Trim range in seconds. ``end`` defaults to the end of the video."""

    start: float = Field(0.0, ge=0)
    end: Optional[float] = Field(None, gt=0)


class ThumbnailResponse(BaseModel):
    upload_id: str
    thumbnail_url: str
    # "video_frame" when extracted from the clip, "upload" when a file was sent.
    source: str
    # The frame's timestamp in the (current, possibly trimmed) clip when the
    # thumbnail was captured from the video; None for an uploaded image.
    at_seconds: Optional[float] = None


# ── AI copy extraction (POST /parse-copy) ─────────────────────────────────────
# One pasted message in, the same per-platform copy structure the upload
# editor already keeps in its state out — so the response can be assigned to
# `platform_copy` (and straight into PostCreate.platform_copy) untouched.
#
# Both schemas are deliberately permissive about *emptiness* and strict about
# *shape*: every platform key is always present (the frontend reads
# response.platform_copy.youtube.title unconditionally), and a missing value
# is an empty string, never a guess. Length is enforced on the way out, in
# services/ai/copy_parser.py, because a limit hit there means the model
# over-ran the source text and the value has to be clipped, not rejected.


class PlatformCopyFields(BaseModel):
    """Title / description / hashtags for one platform."""

    title: str = ""
    description: str = ""
    hashtags: str = ""


class PlatformCopy(BaseModel):
    """The four platforms the scheduler publishes to, always all present."""

    youtube: PlatformCopyFields = Field(default_factory=PlatformCopyFields)
    instagram: PlatformCopyFields = Field(default_factory=PlatformCopyFields)
    tiktok: PlatformCopyFields = Field(default_factory=PlatformCopyFields)
    facebook: PlatformCopyFields = Field(default_factory=PlatformCopyFields)


class ParseCopyRequest(BaseModel):
    """The pasted message to extract platform copy from.

    No ``min_length`` / ``max_length`` here on purpose: the route answers 400
    for an empty message and 413 for an oversized one, because those are
    actionable product errors ("paste something" / "too long") rather than a
    generic 422 with a Pydantic path in it. The caps themselves live in
    ``settings.GROQ_MAX_SOURCE_CHARS``.
    """

    source_text: str = ""

    # A typo'd field name should be a clear 422, not a silently empty request
    # that then 400s for "no text".
    model_config = {"extra": "forbid"}


class ParseCopyResponse(BaseModel):
    platform_copy: PlatformCopy


# ── Manual share targets (Facebook Groups) ───────────────────────────────────
# Meta removed the Groups API on 22 Apr 2024, so a Reel cannot be published
# into a group by the worker. The per-post snapshot (``FacebookGroup``, defined
# above because ``PostCreate`` references it) plus this saved destination list
# are the two halves of the alternative: pick groups on the upload page, then
# work through a checklist once the Reel is live.


class ShareTargetIn(BaseModel):
    """A destination saved for reuse across posts."""

    name: str = Field(..., min_length=1, max_length=120)
    url: str = Field(..., min_length=1, max_length=500)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _clean_group_url(v)


class ShareTargetResponse(BaseModel):
    id: str
    # Always "facebook" today; carried so the list can be filtered later.
    platform: str = "facebook"
    name: str
    url: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ShareTargetDeleteResponse(BaseModel):
    id: str
    message: str


# ── YouTube playlist picker ───────────────────────────────────────────────────


class YouTubePlaylist(BaseModel):
    """One playlist the connected channel owns, for the upload editor's picker."""

    id: str
    title: str
    # "public" | "private" | "unlisted" — shown so a Short is not silently
    # filed into a private collection.
    privacy: str = ""
    item_count: int = 0


class YouTubePlaylistListResponse(BaseModel):
    playlists: list[YouTubePlaylist]
    # Name of the connected channel, so the picker can say whose playlists
    # these are ("" when the connection has no stored account name).
    channel: str = ""


# ── Platform connections ──────────────────────────────────────────────────────


class PlatformAccountResponse(BaseModel):
    """One connected account of a platform, as shown to its owner.

    Never includes token material — only the identifiers and status the UI
    needs to list, pick and disconnect individual accounts.
    """

    # Row id of the connection — what pickers (upload page, inbox) send back
    # to address this exact account.
    id: str
    platform: str
    # The platform-side identity (YouTube channel UC…, IG user id, TikTok
    # open_id, Facebook account id) and its display name.
    account_id: str = ""
    account_name: str = ""
    # Platform extras, e.g. {"google_email": …} for YouTube or
    # {"page_id": …} for Instagram — display only.
    extra_data: dict[str, Any] = Field(default_factory=dict)
    expires_at: Optional[datetime] = None
    # True when the access token is past expiry and no refresh token is held,
    # so publishing with this account would fail — the UI asks for a
    # reconnect.
    reconnect_required: bool = False
    connected_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PlatformConnectionResponse(BaseModel):
    """A platform's connection summary for one user.

    ``accounts`` holds every connected account (possibly several of the same
    platform); the flat ``account_*``/``expires_at`` fields mirror the
    *first* (oldest) account so clients predating multi-account keep working.
    """

    platform: str
    label: str
    # True when at least one account of this platform is connected.
    connected: bool
    # False when the operator has not set this platform's OAuth app
    # credentials; the UI disables the connect button and says why.
    configured: bool
    accounts: list[PlatformAccountResponse] = Field(default_factory=list)
    account_name: str = ""
    account_id: str = ""
    expires_at: Optional[datetime] = None
    # True when ANY connected account's token is past expiry and cannot be
    # renewed unattended.
    reconnect_required: bool = False
    connected_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PlatformAuthUrlResponse(BaseModel):
    platform: str
    auth_url: str


class PlatformDisconnectResponse(BaseModel):
    message: str
    platform: str


# ── Platform app credentials (operator-set DB overrides) ─────────────────────
# Providers name their OAuth app credential fields differently, so a PUT body
# uses each platform's own field names (client_id/client_secret for YouTube,
# app_id/app_secret for Instagram & Facebook, client_key/client_secret for
# TikTok). The response never contains the secret — it is write-only.


class PlatformCredentialsIn(BaseModel):
    """Credentials for one platform; only that platform's pair is accepted.

    ``extra="forbid"`` turns a typo'd field name into a clear 422 instead of
    silently dropping the value the operator just pasted from the console.
    """

    client_id: str = ""
    client_secret: str = ""
    app_id: str = ""
    app_secret: str = ""
    client_key: str = ""

    model_config = {"extra": "forbid"}

    @field_validator(
        "client_id", "client_secret", "app_id", "app_secret", "client_key"
    )
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class PlatformCredentialsResponse(BaseModel):
    """Credential status for one platform — never the secret itself."""

    platform: str
    label: str
    # True when an effective pair exists (DB row or environment).
    configured: bool
    # Where the effective pair comes from: "database" | "environment" | "none".
    source: str = "none"
    # The non-secret identifier when it is stored in the database (empty when
    # the pair comes purely from the environment or nothing is configured).
    identifier: str = ""
    # True when a DB row holds a secret (the value itself is never echoed).
    has_secret: bool = False
    updated_at: Optional[datetime] = None


class PlatformCredentialsMessageResponse(BaseModel):
    message: str
    platform: str


# ── Stats ─────────────────────────────────────────────────────────────────────


class StatsResponse(BaseModel):
    scheduled_this_week: int
    total_scheduled: int
    total_published: int
    total_failed: int
    next_post_at: Optional[datetime] = None
    # Human string such as "in 2 days 3 hours"; kept for the dashboard header.
    next_post_in: Optional[str] = None
    connected_platforms: list[str]
    # Publishes per platform over all time — feeds the history page summary.
    per_platform: dict[str, dict[str, int]] = {}


class CalendarDay(BaseModel):
    date: str  # YYYY-MM-DD (UTC)
    posts: list[PostResponse]


# ── OAuth callback (query params on the platform redirect) ────────────────────


class OAuthCallbackQuery(BaseModel):
    code: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None
    error_description: Optional[str] = None
    extra: dict[str, Any] = {}
