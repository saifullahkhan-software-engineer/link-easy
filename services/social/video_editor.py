"""
Server-side video editing (trim + thumbnail extraction) backed by ffmpeg.

Used by the social-scheduler upload editor so a clip can be trimmed and a
thumbnail chosen — either a frame of the video or an image the user uploads —
before it is scheduled or published. The edited file *replaces* the original
upload on disk, so the publish pipeline (which streams the file at
``social_posts.video_path`` and serves it at ``social_posts.video_url``) needs
no other change: whatever the editor keeps is exactly what gets streamed to
YouTube / Instagram / TikTok / Facebook.

The helpers are deliberately synchronous and free of framework imports so
FastAPI can run them on a worker thread with ``asyncio.to_thread`` and the
Celery worker could reuse them unchanged.

Dependency: the container installs ffmpeg at build time (see the root
Dockerfile). The only hard runtime dependency is the ``ffmpeg`` binary on
PATH. ``FFMPEG_BINARY`` lets a deployment or the test suite point at a
custom binary.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

FFMPEG_BINARY = os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg") or "ffmpeg"
# A trim / frame of a short-form clip should finish in a few seconds; this is a
# guard against a wedged binary, not a budget for normal work.
_EDIT_TIMEOUT = int(os.environ.get("VIDEO_EDIT_TIMEOUT", "300"))

# ffmpeg prints "Duration: HH:MM:SS.micro" on stderr when given only -i.
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", re.IGNORECASE)


class VideoEditError(RuntimeError):
    """Raised when ffmpeg is missing, unreachable, or fails an edit request."""


def ffmpeg_available() -> bool:
    """True when the configured ffmpeg binary exists and is executable."""
    if not FFMPEG_BINARY:
        return False
    if os.path.sep in FFMPEG_BINARY:
        return os.path.isfile(FFMPEG_BINARY) and os.access(FFMPEG_BINARY, os.X_OK)
    return shutil.which(FFMPEG_BINARY) is not None


def _run_ffmpeg(args: list, *, check: bool = True) -> str:
    """Run ffmpeg with the common banner/short-circuit flags and return stderr.

    Returns (rather than writes to a terminal) so callers can raise a useful
    error message that includes ffmpeg's own diagnosis on failure.
    """
    cmd = [FFMPEG_BINARY, "-hide_banner", "-y", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_EDIT_TIMEOUT,
        )
    except FileNotFoundError as exc:  # pragma: no cover - exercised via mocked PATH
        raise VideoEditError(
            "Video editing is unavailable: the ffmpeg binary was not found."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoEditError(
            "Video editing timed out — the clip may be too large to process quickly."
        ) from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or "").strip()[-600:]
        raise VideoEditError(f"Video editing failed (ffmpeg exit {proc.returncode}): {detail}")
    return proc.stderr or ""


def probe_duration(path: str) -> float:
    """Return a media file's duration in seconds (or raise ``VideoEditError``).

    Parsed from ffmpeg's own stderr rather than requiring a separate ``ffprobe``
    binary, so a single ffmpeg install is sufficient everywhere.
    """
    if not os.path.isfile(path):
        raise VideoEditError("The video file is missing.")
    stderr = _run_ffmpeg(["-i", path], check=False)
    match = _DURATION_RE.search(stderr)
    if not match:
        raise VideoEditError("Could not read the video's duration.")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _encode_args(extension: str):
    """Return ffmpeg encode flags matching the container of ``extension``.

    The re-encoded file is written to a temp whose suffix mirrors the original
    upload's extension, so ffmpeg muxes into the matching container: the MP4
    family (``.mp4/.mov/.m4v``) takes h264+aac, while ``.webm`` takes
    VP9/Opus. Normalising every input to these pairs (rather than stream
    copying whatever the camera produced) keeps the result acceptable to every
    short-form publishing target.
    """
    if (extension or "").lower() == ".webm":
        return ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-c:a", "libopus"]
    # .mp4 / .mov / .m4v → h264 + aac (faststart when muxing to MP4).
    return [
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
    ]


def trim_video(source: str, destination: str, start: float, duration: float, extension: str = ".mp4") -> None:
    """Re-encode ``[start, start+duration)`` of ``source`` to ``destination``.

    Re-encoding (rather than stream-copying) normalises the codec/container so
    the resulting file is accepted by every short-form target regardless of the
    original camera's oddities.
    """
    _run_ffmpeg(
        [
            "-ss", f"{start:.3f}",
            "-i", source,
            "-t", f"{max(duration, 0.001):.3f}",
            "-map", "0:v:0",
            "-map", "0:a?",
            *_encode_args(extension),
            destination,
        ]
    )


def extract_frame(source: str, destination: str, at_seconds: float) -> None:
    """Write a JPEG still from ``source`` at ``at_seconds`` to ``destination``."""
    _run_ffmpeg(
        [
            "-ss", f"{at_seconds:.3f}",
            "-i", source,
            "-frames:v", "1",
            "-q:v", "2",
            destination,
        ]
    )


def write_jpeg_thumbnail(data: bytes, destination: str, *, max_dim: int = 1600) -> None:
    """Normalise a user-uploaded image into a JPEG thumbnail on disk.

    Re-encoding through Pillow guarantees a consistent format served at the
    same ``.jpg`` URL regardless of the source (PNG/WebP/GIF/HEIC) and keeps a
    huge 12 MP photo from being stored verbatim. ``max_dim`` caps the longest
    edge; short-form thumbnails are tiny on screen, so 1600 px is plenty.
    """
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise VideoEditError("The thumbnail file could not be read as an image.") from exc

    image = image.convert("RGB")  # JPEG has no alpha; flatten on a black ground.
    image.thumbnail((max_dim, max_dim))
    image.save(destination, "JPEG", quality=85)
    if not os.path.isfile(destination) or os.path.getsize(destination) == 0:
        raise VideoEditError("The thumbnail image could not be saved.")
