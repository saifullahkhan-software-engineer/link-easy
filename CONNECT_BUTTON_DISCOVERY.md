# Connect-button discovery rewrite (structural, evidence-producing)

## Symptom

Worker sessions failed every `send_connection` step with:

```
🔎 Connect not in the top card; checking the More actions menu
⚠️ Connection request failed: Connect button not found on the profile.
```

The ~12 s gap between those two lines (exactly 3 × 4 s) shows that **neither
the Connect button nor the "More actions" trigger matched anything on the
page** — the old code burned ~36 s of serial per-selector timeouts
(4 × 6 s + 3 × 4 s) and then reported failure with **no evidence** of what
LinkedIn actually rendered (new layout variant? follow-first profile?
restricted profile? non-English UI?).

## What changed (`automation/actions/connect.py`)

1. **Structural top-card scan instead of a fixed CSS wish-list.**
   The profile top card is anchored as the `<section>` wrapping `main h1`,
   every interactive node inside it is inventoried with one JS evaluation
   (aria-label, visible text, `data-control-name`, `data-view-name`,
   dropdown classes, visibility/enabled state) and classified in Python
   (`_classify_top_card_action`).  This survives LinkedIn's A/B class-name
   and aria-label churn, and structurally excludes the "People also viewed"
   rail Connect buttons (they live outside the top card — clicking those
   would have connected the wrong person).

2. **Poll-until-deadline instead of serial timeouts.**
   `_poll_top_card_actions` re-scans every 0.4 s until a single deadline
   (14 s), so a rendered button is found in <1 s while a genuinely absent
   one still waits out lazy rendering.  Worst case ≈ 26 s saved per lead.

3. **Overflow-menu scanning.**
   After clicking More, `_poll_menu_connect` picks the *smallest* matching
   node inside any visible `role=menu` / `.artdeco-dropdown__content` /
   popover container (menus are portaled to `<body>`, so lookup is not
   scoped to `main`), filters out `disconnect` / `remove connection` /
   `report` items, and treats a **"Withdraw invitation"** menu item as the
   already-pending state (`already_connected=True`).

4. **Evidence on failure.**
   When nothing matches, `_describe_missing_connect` logs the rendered
   action inventory, page title and LinkedIn UI language, saves
   `connect_no_button_debug.png` + `.html` (development), and embeds the
   inventory in the returned `error` — the next worker log line answers
   "why" instead of just repeating "not found".

5. **Current dialog copy + restriction gates.**
   Explicit "Send without a note" send-button variants, and detection of
   the "enter their email address to connect" restricted-invite dialog
   (`connect_restricted=True` in the result, precise error text).

The result contract used by the worker is unchanged: `sent`, `with_note`,
`error`, plus the optional flags `already_connected`, `page_load_failed`,
`session_stale` (and the new additive `connect_restricted`).

## Verification

- `tests/test_connect_action.py` — 10 unit tests (classification of Connect/
  More variants incl. `data-view-name`, rejection of Follow/Message/
  Disconnect, top-card poll, More-menu path, inventory-backed error,
  Withdraw-means-pending).  Full suite: 29/29 pass.
- The embedded JS snippets were extracted and executed against jsdom
  fixtures: classic artdeco UI, the newer `data-view-name` UI with a
  right-rail "People also viewed" section, portaled `role=menu` menus, and
  bare-span menu items — all resolve the correct node.

## If it still fails

The new error message prints the buttons LinkedIn actually rendered and
(dev mode) the page snapshot.  Non-English LinkedIn UI languages are
detected and called out explicitly in the error — action discovery assumes
an English interface.
