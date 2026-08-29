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

# Create a non-root user.  Do this before declaring VOLUME so Docker's
# anonymous fallback is initialized with an appuser-owned directory.
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app /ms-playwright
USER appuser

# Declare the directory as a Docker volume as well.  A platform-provided
# persistent volume mounted at /app/profiles takes precedence over the
# anonymous fallback created by Docker when no platform volume is configured.
VOLUME ["/app/profiles"]

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
# Hosted demo: set ENVIRONMENT=deployment on the service. start.sh then skips
# Celery Beat (no unattended timers on a free-tier box), the API clears the
# Beat schedule and refuses to arm recurring jobs, and the UI shows the
# "run it locally" banner. The Celery worker still runs, so on-demand actions
# keep working. Any other ENVIRONMENT value — including the default
# "production" — runs the full three-process stack exactly as before.
#
# docker-compose.yml is unaffected: its api / worker / beat services each
# declare their own `command:`, which overrides this CMD.
ENV ENVIRONMENT=production
CMD ["/app/start.sh"]
