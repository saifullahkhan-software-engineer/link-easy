"""
Action: Send a LinkedIn connection request with optional personalised note.
FILE: automation/actions/connect.py
 
Day 2 of the drip sequence. Uses {{first_name}} placeholder substitution
from the campaign's connection_note_template.
"""
import asyncio
import random
from patchright.async_api import Page
from automation.human import human_click, human_type, human_scroll, random_idle_pause
 
 
async def send_connection_request(page: Page, profile_url: str,
                                   first_name: str = None,
                                   note_template: str = None) -> dict:
    """
    Sends a connection request to the LinkedIn profile at profile_url.
 
    note_template: string with optional {{first_name}} placeholder.
    If None, sends without a note (higher acceptance rate for cold outreach).
    """
    result = {"sent": False, "with_note": False, "error": None}
 
    try:
        await page.goto(profile_url, wait_until="domcontentloaded")
        await random_idle_pause(3, 6)
 
        if "/in/" not in page.url:
            result["error"] = f"Not a profile page: {page.url}"
            return result
 
        # Scroll a bit before clicking connect (natural behaviour)
        await human_scroll(page)
        await random_idle_pause(1.5, 4.0)
 
        # Find the Connect button — it may be in the main button area or under "More"
        connect_btn = await page.query_selector(
            "button[aria-label*='Connect']"
        )
 
        if not connect_btn:
            # Try "More" dropdown → Connect
            more_btn = await page.query_selector("button[aria-label='More actions']")
            if more_btn:
                await human_click(page, "button[aria-label='More actions']")
                await random_idle_pause(0.5, 1.5)
                connect_btn = await page.query_selector("div[aria-label*='Connect']")
 
        if not connect_btn:
            result["error"] = "Connect button not found — may already be connected or pending"
            return result
 
        await human_click(page, "button[aria-label*='Connect']")
        await random_idle_pause(1.0, 2.5)
 
        # ── Add a personalised note if template is provided ───────────────────
        if note_template:
            note = note_template.replace("{{first_name}}", first_name or "there")
 
            add_note_btn = await page.query_selector("button[aria-label='Add a note']")
            if add_note_btn:
                await human_click(page, "button[aria-label='Add a note']")
                await random_idle_pause(0.5, 1.5)
 
                await human_type(page, "textarea[name='message']", note)
                await random_idle_pause(0.5, 1.5)
                result["with_note"] = True
 
        # Click Send
        send_btn = await page.query_selector("button[aria-label='Send now']") or                    await page.query_selector("button[aria-label='Send invitation']")
        if send_btn:
            await human_click(page, send_btn)
            await random_idle_pause(2, 4)
            result["sent"] = True
 
    except Exception as e:
        result["error"] = str(e)
 
    return result

