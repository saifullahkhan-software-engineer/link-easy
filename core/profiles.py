"""Helpers for inspecting durable Chromium profile directories.

The profile directory *is* the session for both LinkedIn (one dir per
account) and WhatsApp (one shared dir): Chromium persists cookies,
localStorage and IndexedDB to it continuously, and there is no copy of the
session in the database. If the directory is wiped — a deploy without the
volume mounted is the usual cause — the database still says "connected"
while the next browser launch would land on a blank QR screen.

These helpers let status endpoints detect that split-brain state without
launching a browser.
"""
import os
from typing import Optional


def profile_dir_missing(path: Optional[str]) -> bool:
    """True when the profile dir does not exist or is empty.

    An *empty* directory counts as missing: a connected account writes
    cookies / IndexedDB files into the dir almost immediately, so an empty
    dir under a "connected" row means the durable storage was wiped rather
    than a fresh-but-valid state. A falsy path is reported as missing.

    Never raises: permission/OS errors are reported as "missing" so a status
    endpoint degrades to the safe (loud) answer instead of a 500.
    """
    if not path:
        return True
    try:
        if not os.path.isdir(path):
            return True
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except OSError:
        return True
