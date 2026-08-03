# Fix: White Page Issue in LinkedIn Automation

## Problem

LinkedIn automation was encountering white/blank pages when navigating to profiles. This happens because:

1. **`wait_until="domcontentloaded"` only waits for HTML parsing**, not for JavaScript execution and rendering
2. LinkedIn is a heavy React/SPA application that loads most content dynamically via JavaScript
3. LinkedIn sometimes serves blank pages when:
   - Bot detection triggers
   - Session is stale/expired
   - Challenge/captcha needs to be solved
   - Network issues occur

## Solution

### 1. Created Shared Utility (`automation/actions/utils.py`)

Added a shared utility module with:
- **`is_blank_page(page)`**: Detects blank/white pages by checking:
  - Body text length (< 100 chars = likely blank)
  - Presence of LinkedIn's main app container (`#app-mount` or `.scaffold-layout__main`)
  
- **`navigate_to_profile(page, profile_url)`**: Helper that handles navigation with blank page detection and reload

### 2. Changed Wait Strategy

Updated all `page.goto()` calls from:
```python
await page.goto(url, wait_until="domcontentloaded", timeout=30000)
```

To:
```python
await page.goto(url, wait_until="networkidle", timeout=30000)
```

**Why `networkidle`?**
- `domcontentloaded`: Waits only for HTML to be parsed (JavaScript may still be loading)
- `networkidle`: Waits until there are no network connections for at least 500ms (page fully rendered)

### 3. Added Blank Page Detection & Recovery

After each navigation, the code now:
1. Checks if the page is blank using `is_blank_page()`
2. If blank, reloads the page once with `networkidle`
3. If still blank after reload, takes a screenshot for debugging and returns an error

```python
if await is_blank_page(page):
    logger.warning("⚠️ Blank page detected, reloading...")
    await page.reload(wait_until="networkidle", timeout=30000)
    await random_idle_pause(2, 4)
    
    if await is_blank_page(page):
        # Take screenshot for debugging
        result["error"] = "Page failed to load (blank page after reload). Session may be stale."
        return result
```

## Files Changed

### New File
- **`automation/actions/utils.py`**: Shared utilities for page checks

### Updated Files
- **`automation/actions/connect.py`**: 
  - Added `_is_blank_page()` helper
  - Changed `wait_until="domcontentloaded"` → `"networkidle"`
  - Added blank page detection and reload logic

- **`automation/actions/visit_profile.py`**: 
  - Imported `is_blank_page` from utils
  - Changed `wait_until="domcontentloaded"` → `"networkidle"` (2 locations)
  - Added blank page detection and reload logic

- **`automation/actions/message.py`**: 
  - Imported `is_blank_page` from utils
  - Changed `wait_until="domcontentloaded"` → `"networkidle"` (3 locations)
  - Added blank page detection and reload logic (2 locations)

- **`automation/actions/feed_scroll.py`**: 
  - Imported `is_blank_page` from utils
  - Changed `wait_until="domcontentloaded"` → `"networkidle"`
  - Added blank page detection and reload logic

## Result

✅ **Better page loading**: Waits for full JavaScript rendering, not just HTML  
✅ **Blank page recovery**: Automatically reloads if page is blank  
✅ **Debug visibility**: Takes screenshots when blank page persists  
✅ **Consistent behavior**: All action modules now handle blank pages the same way  
✅ **Shared code**: Common utilities reduce duplication across modules

## Testing

To test the fix:
1. Run a campaign with leads
2. Monitor logs for "Blank page detected" warnings
3. Check if pages reload successfully
4. Verify campaigns continue instead of failing on blank pages
5. Check debug screenshots in `connect_blank_page_debug.png`, etc. (if screenshots are enabled)

## Notes

- The blank page detection is conservative (text < 100 chars + no app container)
- Only one reload attempt is made to avoid infinite loops
- The 30-second timeout prevents hanging on slow pages
- Screenshots are only taken when `should_take_screenshots()` returns True
