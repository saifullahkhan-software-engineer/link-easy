# Use Python 3.14 slim image
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

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

# Create a non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app /ms-playwright
USER appuser

# Expose port
EXPOSE 8000

# Run the application with uvicorn.
# Railway injects a PORT env var and healthchecks it — honor it when present
# (shell form so ${PORT:-8000} expands), defaulting to 8000 elsewhere.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
