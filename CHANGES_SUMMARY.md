# Changes Summary: Backend Logs & WhatsApp Browser Flow

## Changes Made

### 1. Logs Now Go to Terminal (Backend) Instead of Frontend

**Files Modified:**
- `main.py` - Changed middleware to log API calls to terminal instead of `log_hub`
- `core/live_hub.py` - Added `STREAM_LOGS_TO_FRONTEND` env var check to disable frontend log streaming in production
- `api/v1/whatsapp_scanner.py` - Replaced `log_hub.publish()` with regular logger calls
- `services/browser_view.py` - Replaced `log_hub.publish()` with regular logger calls

**Behavior:**
- All logs (API calls, WhatsApp connection, errors) are now written to the terminal/backend
- The `/api/v1/live/logs` SSE endpoint still exists for debugging but will be mostly empty
- To enable frontend log streaming (dev only), set `STREAM_LOGS_TO_FRONTEND=true`

### 2. Browser Only Opens for QR Scan and 2FA

**Files Modified:**
- `api/v1/whatsapp_scanner.py` - Updated `_watch_qr_scan()` to:
  - Stop browser after successful QR scan (no 2FA)
  - Keep browser open if 2FA is detected
  - Stop browser after 2FA is completed
  - Added `_check_2fa_page()` function to detect 2FA screens
- `services/browser_view.py` - Updated comments to reflect the new behavior

**Behavior:**
1. When user clicks "Connect WhatsApp":
   - Browser opens in headless mode
   - QR code is displayed in the browser view
   - User scans QR with their phone

2. After QR scan:
   - **If no 2FA**: Browser stops automatically, session is saved
   - **If 2FA required**: Browser stays open, user enters 6-digit code in the browser view

3. After 2FA (if needed):
   - Browser stops automatically
   - Session is saved with cookies

**Benefits:**
- Server resources are freed when browser is not needed
- Clear terminal logs show the full connection flow
- 2FA is handled seamlessly - browser reopens/stays open for code entry

## Environment Variables

- `STREAM_LOGS_TO_FRONTEND`: Set to `"true"` to enable frontend log streaming (development only)

## Usage

When deploying to a server:
1. Check terminal logs for all application logs and errors
2. WhatsApp browser only runs during connection flow
3. After connection, no browser process is running in the background
