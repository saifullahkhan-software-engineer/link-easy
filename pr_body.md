## Summary

- Logs now go to terminal/backend instead of frontend SSE stream for easy monitoring on servers
- Browser only opens for WhatsApp QR scan and 2FA code entry
- Browser stops automatically after successful connection or 2FA completion
- Added STREAM_LOGS_TO_FRONTEND env var for debugging (dev only)

## Changes

### 1. Backend Logs to Terminal
- Modified `main.py` middleware to log API calls to terminal using Python's logging
- Updated `core/live_hub.py` to disable frontend log streaming by default
- Replaced all `log_hub.publish()` calls with regular logger calls

### 2. Browser Only for QR/2FA
- Updated `_watch_qr_scan()` to stop browser after successful QR scan
- Added `_check_2fa_page()` to detect 2FA screens
- Browser stays open if 2FA required, stops after code entry

## Testing

Tested locally by:
1. Starting the application
2. Checking terminal logs show API calls
3. Connecting WhatsApp and verifying browser opens/closes appropriately
