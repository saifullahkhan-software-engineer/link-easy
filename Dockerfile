# Use Python 3.14 slim image
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# The runtime volume (when attached) and the ephemeral fallback use the same
# path, so every process in the container resolves profiles consistently.
ENV PROFILE_STORAGE_DIR=/app/profiles

# Set working directory
WORKDIR /app

# Install system dependencies required for Playwright + Tesseract OCR
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    libleptonica-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Patchright (stealth Playwright fork) Chromium browser + system deps.
# `playwright` was replaced by `patchright` in requirements.txt, so the
# matching CLI is `patchright`, not `playwright`.
#
# Browsers are installed into a FIXED shared path (PLAYWRIGHT_BROWSERS_PATH),
# NOT the root user's ~/.cache/ms-playwright: the app runs as the non-root
# `appuser` at runtime, and without this it cannot see the root-owned
# browsers and fails on the first browser launch (WhatsApp connect /
# LinkedIn session) with:
#   Executable doesn't exist at /home/appuser/.cache/ms-playwright/...
# `patchright install chromium` installs BOTH the full Chromium build and
# the chromium-headless-shell (the one used for headless launches), so one
# command is enough.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN patchright install chromium --with-deps

# Copy application code
COPY . .

# The entrypoint must be executable even when the checkout lost the +x bit
# (e.g. cloned on Windows, where git does not preserve the mode).
RUN chmod +x /app/start.sh

# Always have a usable mount point in the image.  Railway volumes are added
# to the service separately and replace this directory at runtime; when no
# volume is attached, this image-owned directory is an intentional ephemeral
# fallback so the browser can create fresh profiles instead of failing at boot.
RUN mkdir -p /app/profiles && chmod 700 /app/profiles

# Create the non-root user and hand /app over to it, so the image-owned
# /app/profiles fallback directory is writable by the runtime user even
# before any platform volume is attached.
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app /ms-playwright
USER appuser

# NOTE: deliberately NO `VOLUME ["/app/profiles"]` instruction here.
# Railway's Dockerfile builder rejects docker VOLUME directives
# ("dockerfile invalid: docker VOLUME ... is not supported, use Railway
# Volumes"). Persistent storage is instead attached as a Railway volume in
# the service's Volumes settings, mounted at exactly /app/profiles; it
# shadows the image-owned directory above at runtime. When no volume is
# attached, start.sh re-creates the directory and the app runs with fresh,
# ephemeral profiles instead of failing the deploy.

# Expose port
EXPOSE 8000

# Default runtime: API + Celery worker + Celery Beat in ONE container.
#
# All three need the SAME durable Chromium profile directory
# (PROFILE_STORAGE_DIR): the API owns the browser while LinkedIn/WhatsApp are
# being connected, and the worker reopens those profiles for campaign
# sessions, feed scrolls and WhatsApp scans. A Railway volume attaches to a
# single service, so running them as separate services would give the worker
# an empty profile (QR screen) instead of the live session.
#
# start.sh honours ${PORT} (Railway injects it) and can be narrowed with
# RUN_WEB / RUN_WORKER / RUN_BEAT.
#
# Hosted instance: set ENVIRONMENT=deployment on the service. This now runs
# the FULL three-process stack (API + Celery worker + Celery Beat), so hosted
# users can connect accounts, create campaigns and feed-scan jobs and start
# them: Beat advances campaign drip steps and fires recurring scans from the
# database-backed schedule. The "hosted instance" banner is the only
# deployment-specific UI. Timers can still be switched off for an instance
# with SCHEDULED_JOBS_ENABLED=false (API) plus RUN_BEAT=0 (start.sh), and the
# LinkedIn surfaces have their own LINKEDIN_ENABLED kill switch.
#
# docker-compose.yml is unaffected: its api / worker / beat services each
# declare their own `command:`, which overrides this CMD.
ENV ENVIRONMENT=production
CMD ["/app/start.sh"]
