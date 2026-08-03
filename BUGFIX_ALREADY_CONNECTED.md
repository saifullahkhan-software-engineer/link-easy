# Bug Fix: Connection Request Failed for Already Connected Leads

## Problem

When the LinkedIn automation tried to send a connection request to a lead who was already connected or had a pending invitation, the system would:

1. Detect that the Connect button was unavailable
2. Report this as a **failure**: "Connection request failed: Connect button not available — the lead is already connected, or an invitation is already pending."
3. Mark the lead status as `FAILED`
4. Stop the campaign for that lead

This was incorrect because being already connected or having a pending invitation is actually a **good state** — it means the desired outcome is already achieved.

## Root Cause

In `automation/actions/connect.py`, the `_open_connect_dialog()` function returned an error when the Connect button wasn't found. The error was then treated as a failure by `_action_outcome()` in `worker/tasks/campaign_tasks.py`, which marked the lead as `FAILED`.

The issue was that there was no distinction between:
- **Genuine failures** (button not found, LinkedIn error, etc.)
- **Benign conditions** (already connected, invitation already pending)

## Solution

### 1. Added `already_connected` flag to connect action result

Modified `_open_connect_dialog()` to return a third value indicating whether the Connect button is unavailable because the lead is already connected or has a pending invitation:

```python
async def _open_connect_dialog(page: Page) -> tuple[bool, str | None, bool]:
    """Click Connect (top card, else More menu).  Returns ``(opened, error, already_connected)``."""
```

### 2. Propagated the flag through `send_connection_request()`

When the Connect button is unavailable due to an already-connected state, the result now includes:

```python
result["already_connected"] = True
```

### 3. Updated `_action_outcome()` to treat it as success

Added a check in `_action_outcome()` that treats `already_connected` as a non-failure:

```python
# "Already connected" or "invitation already pending" is not a failure —
# the desired end state is already reached.  Treat it as success so the
# campaign can continue to the next step.
if result.get("already_connected"):
    return True, f"{_action_label(step_type)} skipped: {error}", None
```

## Result

Now when a lead is already connected or has a pending invitation:

✅ **Job status**: `DONE` (not `FAILED`)  
✅ **Lead status**: `REQUESTED` (continues normally)  
✅ **Campaign**: Continues to the next step  
✅ **UI display**: "Completed · Connection Request" (green, not red)  
✅ **Message**: "Connection request skipped: Connect button not available — the lead is already connected, or an invitation is already pending."

The campaign no longer stops for leads who are already connected — it simply acknowledges the state and moves forward.

## Files Changed

- `automation/actions/connect.py`: Added `already_connected` flag to return values
- `worker/tasks/campaign_tasks.py`: Updated `_action_outcome()` to handle the flag
