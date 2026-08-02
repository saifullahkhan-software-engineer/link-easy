"""
Action: Visit a LinkedIn profile and optionally like their most recent post.
FILE: automation/actions/like_post.py

DEPRECATED shim.  This module used to hold a second, drifting copy of the
visit+like logic (missing the "no posts", "already liked", intercepted-click
and profile-unavailable cases that automation/actions/visit_profile.py
handles).  Two implementations of the same action meant fixes only ever
landed in one of them, so this module now re-exports the canonical
implementations.

Import from ``automation.actions.visit_profile`` in new code.
"""
from automation.actions.visit_profile import (  # noqa: F401
    like_recent_post,
    visit_profile,
    visit_profile_and_like_post,
)

__all__ = ["visit_profile", "like_recent_post", "visit_profile_and_like_post"]
