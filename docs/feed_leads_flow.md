# Feed Leads — from a scanned post to a campaign lead

> **Feature:** "Add to Lead" on Feed Scroll results + Feed Leads as a third
> lead-intake option on a campaign
> **Status:** Implemented
> **Date:** 2026-08-06

---

## 1. What it does

A post card in **Feed Scroll → results** now has an **Add to Lead** action next
to Like / Comment / Repost / Send.

Saving a post's author does **not** put them straight into a campaign. It
stages the profile in a **Feed Leads list** (one list per feed scroll job).
Later, when leads are added to a campaign, "Feed leads" sits next to
"Manual leads" and "Load CSV" as the third option, and the user picks which
staged profiles to pull in.

```
Feed Scroll results ──"Add to Lead"──▶  Feed Leads list (per scan job)
                                              │
                     campaign ▸ Feed leads tab │  multi-select ▸ "Add N leads"
                                              ▼
                                       leads table (status=pending)
                                       source = job_feed_scan
```

The pool behaves like an inbox: an imported entry is marked `imported` and
disappears from the waiting list, so the list empties as it is used.

---

## 2. Data captured from a post card

| Field | Source on the card |
|---|---|
| `first_name`, `last_name` | `author_first_name`/`author_last_name`, else the display name is split |
| `linkedin_url` | the verified author profile link (personal `/in/` profiles only) |
| `headline` | if present on the card, otherwise omitted (optional, same as CSV) |
| `source` | always `job_feed_scan` |
| `source_post_url` | the post the match came from |
| `matched_score` | the relevance score shown on the card |
| `matched_criteria` | the matched-criteria tags |
| `scan_id` | `scan_batch_id` of the scan run |
| `label` | optional free-text label typed in the save popover |

Source metadata is never shown as part of the lead itself — it is kept for
analytics and surfaced as a small tag/tooltip in Manage Leads.

---

## 3. API

| Method & path | Purpose |
|---|---|
| `POST /api/v1/feed-leads` | Save a scanned profile into a list. **409** if it is already waiting in that list. |
| `GET /api/v1/feed-leads?owner_email=&feed_scroll_job_id=&status=` | List entries (`status` defaults to `saved`). |
| `GET /api/v1/feed-leads/pools?owner_email=&only_with_saved=` | Lists (feed scroll jobs) with waiting/consumed counts. |
| `DELETE /api/v1/feed-leads/{id}?owner_email=` | Discard a staged profile. |
| `POST /api/v1/campaigns/{id}/leads/import-feed-leads` | Bulk-import selected entries into a campaign. |
| `POST /api/v1/campaigns/{id}/leads/quick-add` | Single-profile add straight into a campaign. **409** on duplicates. |

`import-feed-leads` returns three buckets so the UI can report precisely what
happened:

```jsonc
{
  "campaign_id": "…", "campaign_name": "Q3 Founders",
  "added":      [ /* LeadResponse … */ ],
  "duplicates": [ { "feed_lead_id": "…", "reason": "duplicate",
                    "message": "Already in Q3 Founders leads" } ],
  "errors":     [ { "feed_lead_id": "…", "reason": "invalid|not_found",
                    "message": "…" } ]
}
```

Duplicates are consumed from the list (the intent — "this person is in that
campaign" — is already satisfied) but reported explicitly; invalid entries stay
in the list so they can be fixed or discarded.

---

## 4. One lead pathway, not two

Every intake route — manual form, CSV upload, feed-lead import, quick-add —
goes through the same two helpers:

* `schemas.lead.validate_lead_fields()` — required `first_name`, `last_name`,
  `linkedin_url`; the URL must be `https://www.linkedin.com/in/…` and is
  normalised (trimmed, trailing slash stripped).
* `api.v1.leads.build_lead()` — one row in `leads`, `status=pending`,
  `current_step=1`, first action scheduled immediately.

They differ only in the recorded `source`: `manual`, `csv_import`,
`job_feed_scan`.

---

## 5. Schema

`feed_leads` (new) — the pool. Key columns: `owner_email`,
`feed_scroll_job_id` (the list), `linkedin_url`, name/headline, `label`, the
source metadata above, `status` (`saved` | `imported`) and
`imported_campaign_id` / `imported_lead_id` / `imported_at`.

`leads` (extended) — `source`, `source_post_url`, `matched_score`,
`matched_criteria`, `scan_id`. Rows created before this feature keep
`source = NULL` and render without a tag.

Migration: `d4a1c7b8e920_add_feed_leads_pool_and_lead_source` — idempotent, so
it is safe on databases already created with `Base.metadata.create_all()`.

---

## 6. UI states on a post card

| State | Button |
|---|---|
| Not saved | **Add to Lead** → popover: list picker (current scan pre-selected, last choice remembered), optional label, `+ New Campaign` deep link |
| Saved (this session or per the server) | disabled **Added ✓** |
| Already consumed by a campaign | disabled **In campaign** |
| Non-personal profile (company/school page) | disabled, explains only `/in/` profiles can be leads |

Saved state survives a re-scan of the same post: the results page loads the
list snapshot for the job, and session marks cover saves made in the current
tab before a refresh.

---

## 7. Tests

`tests/test_feed_leads_api.py` runs the routers against in-memory SQLite and
covers saving, per-list dedupe (409), pool counts, discarding, import
(creation + consumption + duplicates + unknown ids), ownership checks,
quick-add (creation, 409, validation) and the shared validator.

`frontend/smoke-test.mjs` asserts the results page renders **Add to Lead** and
switches to **Added ✓** when the profile is already staged.
