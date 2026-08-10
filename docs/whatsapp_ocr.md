# WhatsApp OCR setup

WhatsApp image scanning uses `pytesseract` as a Python wrapper around the
native **Tesseract executable**. Tesseract is not an HTTP service and it does
not connect to Redis. It must be installed in the same environment that runs
the Celery worker (`celery -A worker.celery_app worker ...`).

## Local worker

The development compose file intentionally starts only Redis. If FastAPI and
Celery are running directly on the host, install Tesseract on the host too.

### Windows

1. Install Tesseract OCR, normally to
   `C:\Program Files\Tesseract-OCR\tesseract.exe`.
2. Either add `C:\Program Files\Tesseract-OCR` to the **PATH** used by the
   terminal that starts Celery, or set the explicit path before starting the
   worker:

   ```powershell
   $env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
   celery -A worker.celery_app worker --loglevel=info --pool=solo
   ```

   The scanner also checks the usual Windows install directory automatically.
3. Verify it from the same terminal:

   ```powershell
   tesseract --version
   python -c "import shutil; print(shutil.which('tesseract'))"
   ```

   Restart the Celery worker after changing PATH or `TESSERACT_CMD`; a worker
   keeps its environment from the moment it starts.

### Debian/Ubuntu/WSL

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng
tesseract --version
celery -A worker.celery_app worker --loglevel=info
```

## Docker worker

The repository `Dockerfile` already installs `tesseract-ocr` and
`tesseract-ocr-eng`. If the worker is started with Docker Compose, rebuild the
image after changing the Dockerfile:

```bash
docker compose build worker
# The full compose file also requires its postgres and redis services.
docker compose up worker
```

Do not add an OCR container just to fix this error: a separate container would
not be visible to the local Python process. If only `redis` is in Docker, the
local worker still needs a host Tesseract installation.

## What the log means

`OCR failed: tesseract binary not found on PATH` means the worker could import
`pytesseract`, but could not find the native executable. It is independent of
the Redis connection.

`Using Tesseract executable: C:\Program Files\Tesseract-OCR\tesseract.exe`
means installation and path discovery **already succeeded**. If that is followed
by an `OCR skipped 32x72 icon/thumbnail` message, the browser captured an icon or
a low-resolution WhatsApp preview instead of the message image; reinstalling or
changing PATH will not fix that input. The scraper now selects one canonical DOM
container per logical message, rejects small avatars/icons, captures both the
blob and the rendered image, and sends the higher-resolution copy to OCR.

A successful scan logs `OCR extracted N characters`. A Tesseract process,
permission, or language-data failure is logged explicitly as `Tesseract
invocation failed ...` rather than being reported as an ordinary zero-character
result.
