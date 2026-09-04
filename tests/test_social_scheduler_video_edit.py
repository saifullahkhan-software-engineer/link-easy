"""
Social scheduler: server-side video editing - trim and thumbnail endpoints.

These live behind the real router on an in-memory database. They need a real
ffmpeg to actually re-encode / grab a frame, so:

  * the module resolves an ffmpeg binary up front (PATH first, then the
    imageio-ffmpeg wheel if it happens to be installed) and points the
    service at it;
  * every test is skipped when no binary can be found - useful in bare CI
    runners where the Docker image's apt-installed ffmpeg is absent.

Covered behaviour:

  * trimming re-encodes [start,end) *in place* (same upload_id / public
    URL) so the worker later streams exactly the kept range, and reports the
    new duration/size;
  * a degenerate range is rejected instead of silently doing nothing;
  * a thumbnail can be extracted from the video at a chosen second;
  * a thumbnail can be uploaded from the user's PC (validated + normalised);
  * a non-image upload and an unknown upload id are both rejected cleanly.
"""
import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test-secret")


def _find_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # imageio-ffmpeg bundles a static binary for local/dev runs
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = _find_ffmpeg()
if FFMPEG:
    os.environ["FFMPEG_BINARY"] = FFMPEG

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from api.dependencies import get_current_user, get_db  # noqa: E402
from api.v1.social_scheduler import router  # noqa: E402
from core.config import settings  # noqa: E402
from database import Base  # noqa: E402
import models  # noqa: E402,F401
from models.user import User  # noqa: E402

OWNER = "owner@test.dev"


def _user(email):
    return User(first_name="T", last_name="U", email=email, hashed_password="x", is_verified=True, role="customer")


def _render_test_clip(directory: str) -> bytes:
    """Render a tiny real MP4 with a visible counter so duration/size are sane."""
    out = os.path.join(directory, "clip.mp4")
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", "testsrc=duration=2.5:size=320x240:rate=10",
            "-pix_fmt", "yuv420p",
            out,
        ],
        check=True,
    )
    with open(out, "rb") as handle:
        return handle.read()


@unittest.skipUnless(FFMPEG, "ffmpeg binary not available; skipping video-edit tests")
class SocialVideoEditTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.upload_dir = tempfile.mkdtemp(prefix="le-videedit-")
        self.media_dir = tempfile.mkdtemp(prefix="le-media-")
        settings.UPLOAD_DIR = self.upload_dir
        settings.PUBLIC_API_URL = "https://api.example.com"

        app = FastAPI()
        app.include_router(router)
        self.current_email = OWNER

        async def override_get_db():
            async with self.Session() as session:
                yield session

        async def override_user():
            async with self.Session() as session:
                return (await session.execute(select(User).where(User.email == self.current_email))).scalar_one()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_user
        self.app = app
        self.loop.run_until_complete(self._seed())

    def tearDown(self):
        shutil.rmtree(self.upload_dir, ignore_errors=True)
        shutil.rmtree(self.media_dir, ignore_errors=True)
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    async def _seed(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.Session() as s:
            s.add_all([_user(OWNER)])
            await s.commit()

    def run_async(self, fn):
        async def runner():
            async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
                return await fn(client)

        return self.loop.run_until_complete(runner())

    async def _upload_clip(self, client):
        content = _render_test_clip(self.media_dir)
        res = await client.post(
            "/api/v1/social-scheduler/upload", files={"file": ("clip.mp4", content, "video/mp4")}
        )
        assert res.status_code == 200, res.text
        return res.json()

    def _post_trim(self, client, upload_id, start=0.0, end=None):
        body = {"start": start}
        if end is not None:
            body["end"] = end
        return client.post(f"/api/v1/social-scheduler/uploads/{upload_id}/trim", json=body)

    # trim

    def test_trim_reencodes_kept_range_in_place(self):
        async def run(client):
            uploaded = await self._upload_clip(client)
            upload_id = uploaded["upload_id"]
            self.assertIsNotNone(uploaded.get("duration_seconds"))
            self.assertAlmostEqual(uploaded["duration_seconds"], 2.5, delta=0.4)

            res = await self._post_trim(client, upload_id, start=0.4, end=1.4)
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertEqual(data["upload_id"], upload_id, "trim replaces the clip, keeping its id")
            self.assertAlmostEqual(data["duration_seconds"], 1.0, delta=0.3)
            path = os.path.join(self.upload_dir, upload_id)
            self.assertTrue(os.path.isfile(path))

        self.run_async(run)

    def test_trim_full_range_is_a_noop_keeping_original(self):
        async def run(client):
            uploaded = await self._upload_clip(client)
            res = await self._post_trim(client, uploaded["upload_id"], start=0.0, end=99.0)
            self.assertEqual(res.status_code, 200, res.text)
            self.assertAlmostEqual(res.json()["duration_seconds"], 2.5, delta=0.4)

        self.run_async(run)

    def test_trim_rejects_degenerate_ranges(self):
        async def run(client):
            uploaded = await self._upload_clip(client)
            upload_id = uploaded["upload_id"]
            res = await self._post_trim(client, upload_id, start=1.0, end=0.5)
            self.assertEqual(res.status_code, 400)
            res = await self._post_trim(client, upload_id, start=10.0)
            self.assertEqual(res.status_code, 400)
            res = await self._post_trim(client, "0" * 32 + ".mp4", start=0.0, end=1.0)
            self.assertEqual(res.status_code, 404)

        self.run_async(run)

    # thumbnail

    async def _thumb_file_count(self):
        return len([name for name in os.listdir(self.upload_dir) if name.endswith(".thumb.jpg")])

    def test_thumbnail_from_video_frame(self):
        async def run(client):
            uploaded = await self._upload_clip(client)
            res = await client.post(
                f"/api/v1/social-scheduler/uploads/{uploaded['upload_id']}/thumbnail",
                data={"at": "0.8"},
            )
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertEqual(data["source"], "video_frame")
            self.assertAlmostEqual(data["at_seconds"], 0.8)
            self.assertIn("/uploads/social/", data["thumbnail_url"])
            self.assertTrue(data["thumbnail_url"].endswith(".thumb.jpg"))
            self.assertEqual(await self._thumb_file_count(), 1)
            thumb_path = os.path.join(self.upload_dir, data["thumbnail_url"].rsplit("/", 1)[-1])
            self.assertTrue(os.path.isfile(thumb_path))
            with open(thumb_path, "rb") as handle:
                self.assertTrue(handle.read(3).startswith(b"\xff\xd8\xff"))

        self.run_async(run)

    def test_thumbnail_from_uploaded_image(self):
        async def run(client):
            uploaded = await self._upload_clip(client)
            import io

            try:
                from PIL import Image
            except ImportError:
                self.skipTest("Pillow not installed")
            buf = io.BytesIO()
            Image.new("RGB", (4, 4), (200, 30, 90)).save(buf, format="PNG")

            res = await client.post(
                f"/api/v1/social-scheduler/uploads/{uploaded['upload_id']}/thumbnail",
                files={"file": ("cover.png", buf.getvalue(), "image/png")},
            )
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertEqual(data["source"], "upload")
            self.assertIsNone(data["at_seconds"])
            self.assertTrue(data["thumbnail_url"].endswith(".thumb.jpg"))
            thumb_path = os.path.join(self.upload_dir, data["thumbnail_url"].rsplit("/", 1)[-1])
            with open(thumb_path, "rb") as handle:
                self.assertTrue(handle.read(3).startswith(b"\xff\xd8\xff"), "stored as JPEG")

        self.run_async(run)

    def test_thumbnail_rejects_non_images_and_unknown_uploads(self):
        async def run(client):
            uploaded = await self._upload_clip(client)
            res = await client.post(
                f"/api/v1/social-scheduler/uploads/{uploaded['upload_id']}/thumbnail",
                files={"file": ("evil.txt", b"hello", "text/plain")},
            )
            self.assertEqual(res.status_code, 400, res.text)
            res = await client.post(
                "/api/v1/social-scheduler/uploads/" + "0" * 32 + ".mp4/thumbnail",
                data={"at": "0.1"},
            )
            self.assertEqual(res.status_code, 404, res.text)

        self.run_async(run)


if __name__ == "__main__":
    unittest.main()
