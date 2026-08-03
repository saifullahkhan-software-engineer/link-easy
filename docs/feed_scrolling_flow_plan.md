# 📋 Feed Scrolling Flow — Implementation Plan

> **Feature:** Post Scrolling / Feed Scrolling Automation  
> **Status:** Plan — Not Yet Implemented  
> **Date:** 2026-08-03

---

## 1. Overview

A new automation flow that periodically visits the LinkedIn feed, scrolls through posts, scores them based on user-defined criteria, and presents the top 10 scored posts on a dedicated screen for the user to review.

### Two Modes

| Mode | Description |
|------|-------------|
| **Job Search** | User provides structured criteria (experience interval, job titles, skill set). Posts matching job-relevant keywords are scored higher. |
| **Post Search** | User provides freeform keywords/topics. The feed is scanned for posts matching those topics. |

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│  USER CONFIGURES FEED SCROLL JOB                                    │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ Mode: [Job Search ▼] or [Post Search ▼]                      │  │
│  │                                                               │  │
│  │ ── If Job Search ──                                          │  │
│  │   Experience Interval: [2] to [3] years                       │  │
│  │   Job Titles: [Software Engineer, Python Developer, ...]      │  │
│  │   Skill Set: [Database Design, Development, ...]              │  │
│  │                                                               │  │
│  │ ── If Post Search ──                                         │  │
│  │   Keywords/Topics: [freeform text]                            │  │
│  │                                                               │  │
│  │ Feed Visit Interval: [1] hour(s)                              │  │
│  │ LinkedIn Account: [select account ▼]                          │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SCHEDULER (Celery Beat)                                            │
│  Fires a task every N hours based on interval setting               │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  FEED SCROLLING TASK (Celery Worker)                                │
│  1. Launch persistent browser for the LinkedIn account              │
│  2. Navigate to linkedin.com/feed/                                  │
│  3. Scroll naturally (human_scroll) collecting post text             │
│  4. For each post → run regex scoring engine                        │
│  5. Store top 10 posts in DB with scores                            │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SCORED POSTS SCREEN (Frontend)                                     │
│  Scrollable list of 10 posts ranked by score                        │
│  Each post shows: preview text, score, matched keywords, timestamp  │
│  (Future: AI scoring replaces regex)                                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Database Models (New)

### 2.1 `feed_scroll_job` — The user's configured feed scroll automation

Stores the user's configuration: mode, criteria, interval.

```
feed_scroll_jobs
├── id                    (String PK — UUID)
├── account_email         (String FK → linkedin_accounts.linkedin_email)
├── user_email            (String FK → users.email)
├── name                  (String)                    — e.g. "Backend Job Hunt"
├── mode                  (Enum: "job_search" | "post_search")
├── status                (Enum: "active" | "paused" | "draft")
│
│   ── Job Search criteria (JSON) ──
├── experience_min_years  (Integer, nullable)         — e.g. 2
├── experience_max_years  (Integer, nullable)         — e.g. 3
├── job_titles            (JSON, nullable)             — ["Software Engineer", "Python Developer"]
├── skill_set             (JSON, nullable)             — ["database design", "development"]
│
│   ── Post Search criteria (JSON) ──
├── keywords              (JSON, nullable)             — ["AI", "machine learning", "data science"]
│
├── feed_interval_hours   (Integer, default=1)         — How often to visit the feed (1, 2, 4, etc.)
├── posts_per_scan        (Integer, default=10)        — How many top posts to keep per scan
│
├── last_scanned_at       (DateTime, nullable)
├── next_scan_at          (DateTime, nullable)
├── created_at            (DateTime)
└── updated_at            (DateTime)
```

### 2.2 `feed_scroll_result` — Individual scored posts

Each scan produces up to N result rows.

```
feed_scroll_results
├── id                    (String PK — UUID)
├── feed_scroll_job_id    (String FK → feed_scroll_jobs.id)
├── post_urn              (String)                     — LinkedIn post URN / ID (dedup key)
├── post_url              (String, nullable)           — Full LinkedIn URL to the post
├── author_name           (String, nullable)           — Who posted it
├── post_text             (Text)                       — Extracted post text
├── score                 (Float)                      — Regex match score (0.0 – 100.0)
├── matched_terms         (JSON)                       — Which keywords/titles/skills matched
├── scan_batch_id         (String)                     — Groups posts from the same scan run
├── scanned_at            (DateTime)
└── created_at            (DateTime)
```

---

## 3. Scoring Engine (Regex-Based — Phase 1)

The scoring engine is a standalone module so it can later be swapped for an AI-based scorer.

### 3.1 Location

```
automation/scoring/feed_scorer.py
```

### 3.2 Algorithm

```python
def score_post(post_text: str, config: dict) -> (float, list[str]):
    """
    Returns (score 0-100, list_of_matched_terms).
    
    Scoring breakdown:
    ┌─────────────────────────────────────────────────────────────┐
    │  Category          │ Weight   │ Match Method               │
    ├────────────────────┼──────────┼────────────────────────────┤
    │  Job Titles        │ 35 pts   │ Case-insensitive regex     │
    │  Skills            │ 30 pts   │ Case-insensitive regex     │
    │  Experience Level  │ 20 pts   │ Regex for year ranges      │
    │                    │          │ ("2+ years", "3-5 years")  │
    │  Keywords (post)   │ 15 pts   │ Case-insensitive regex     │
    └─────────────────────────────────────────────────────────────┘
    
    Each category score = (matches / total_terms_in_category) × category_weight
    Total score = sum of all category scores (capped at 100)
    """
```

### 3.3 Regex Patterns (Examples)

| Input | Regex Pattern | Matches |
|-------|---------------|---------|
| `"Software Engineer"` | `(?i)\bsoftware\s*engineer\b` | "Software Engineer", "software engineer" |
| `"2 to 3 years"` | `(?i)(\b2[\s-]*(?:to|-)[\s-]*3\b|\b2\+?\s*years|\b3\s*years)` | "2-3 years", "2 to 3 years", "2+ years" |
| `"database design"` | `(?i)\bdatabase\s*design\b` | "database design", "Database Design" |

### 3.4 Future: AI Scorer Interface

```python
class ScorerInterface(Protocol):
    async def score(self, post_text: str, config: dict) -> tuple[float, list[str]]: ...

class RegexScorer(ScorerInterface): ...   # Phase 1
class AIScorer(ScorerInterface): ...      # Phase 2 (future)
```

---

## 4. Automation Action (New)

### 4.1 Location

```
automation/actions/feed_scroll.py
```

### 4.2 Responsibilities

```python
async def scroll_feed_and_collect(
    page: Page,
    num_posts: int = 20,        # Scroll until ~20 posts collected
) -> list[dict]:
    """
    Navigate to linkedin.com/feed/
    Scroll naturally using human_scroll()
    Extract post elements: text, author, URN, URL
    Return list of post dicts
    """
```

### 4.3 Post Extraction Strategy

```
1. Navigate to https://www.linkedin.com/feed/
2. Wait for feed container to load (selector: "div.feed-shared-update-v2" or similar)
3. For each scroll iteration:
   a. human_scroll(page) — natural scroll
   b. random_idle_pause(1.5, 3) — read the post
   c. Collect all visible post elements (deduplicate by data-urn)
4. Stop after collecting target number of posts or max scrolls
5. Return structured post data
```

---

## 5. Celery Task & Scheduler

### 5.1 New Task

```
worker/tasks/feed_scroll_tasks.py
```

### 5.2 Task: `run_feed_scroll`

```python
@celery_app.task(bind=True)
def run_feed_scroll(self, feed_scroll_job_id: str):
    """
    1. Acquire playwright semaphore
    2. Launch persistent browser for the linkedin account
    3. Verify session (verify_session)
    4. Navigate to feed, scroll, collect posts
    5. Score each post using feed_scorer
    6. Store top N results in DB (feed_scroll_results)
    7. Update feed_scroll_job.last_scanned_at & next_scan_at
    8. Schedule next run based on feed_interval_hours
    9. Close browser
    """
```

### 5.3 Scheduling

Two options (recommend **Option B**):

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| **A** | Celery Beat static schedule | Simple | All jobs run at same cadence, inflexible |
| **B** | Self-rescheduling (task schedules its own next run) | Per-job intervals, flexible | Slightly more complex |

**Option B** mirrors how campaign tasks already work — each `run_feed_scroll` task, upon completion, schedules its own next execution based on `feed_interval_hours` + jitter.

---

## 6. Backend API (New Endpoints)

### 6.1 Location

```
api/v1/feed_scroll.py
```

### 6.2 Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/feed-scroll/jobs` | Create a new feed scroll job |
| `GET` | `/api/v1/feed-scroll/jobs` | List all feed scroll jobs for the user |
| `GET` | `/api/v1/feed-scroll/jobs/{id}` | Get a single job with its config |
| `PATCH` | `/api/v1/feed-scroll/jobs/{id}` | Update job (pause/resume/change interval) |
| `DELETE` | `/api/v1/feed-scroll/jobs/{id}` | Delete a feed scroll job |
| `GET` | `/api/v1/feed-scroll/jobs/{id}/results` | Get scored posts for a job (latest scan or paginated) |
| `POST` | `/api/v1/feed-scroll/jobs/{id}/scan` | Trigger an immediate manual scan |

### 6.3 Pydantic Schemas

```
schemas/feed_scroll.py
├── FeedScrollJobCreate      — Input to create a job
├── FeedScrollJobUpdate      — Input to update a job  
├── FeedScrollJobResponse    — Job returned to frontend
├── FeedScrollResultResponse — Single scored post returned to frontend
```

---

## 7. Frontend (New Pages & Components)

### 7.1 New Route

```
/app/feed-scroll                  → FeedScrollJobsPage (list all jobs)
/app/feed-scroll/create           → FeedScrollCreatePage (create new job)
/app/feed-scroll/jobs/:id         → FeedScrollResultsPage (view scored posts)
```

### 7.2 Pages

#### Page 1: Feed Scroll Jobs List (`FeedScrollJobsPage.jsx`)

```
┌─────────────────────────────────────────────────────────┐
│  Feed Scroll Jobs                              [+ New]  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │ 🔍 Backend Job Hunt                             │   │
│  │ Mode: Job Search | Interval: 1h | Status: Active │   │
│  │ Last scan: 25 min ago | Next scan: in 35 min    │   │
│  │ Latest top score: 87/100                         │   │
│  │                                    [View] [⏸]   │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │ 📝 AI Trends Tracker                            │   │
│  │ Mode: Post Search | Interval: 2h | Status: Paused│   │
│  │ Last scan: 3 hours ago                          │   │
│  │ Latest top score: 64/100                         │   │
│  │                                    [View] [▶]   │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

#### Page 2: Create Feed Scroll Job (`FeedScrollCreatePage.jsx`)

```
┌─────────────────────────────────────────────────────────┐
│  Create Feed Scroll Job                                 │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Job Name: [________________________]                   │
│  LinkedIn Account: [select account ▼]                   │
│                                                         │
│  Mode:  ◉ Job Search   ○ Post Search                   │
│                                                         │
│  ── Job Search Configuration ──                        │
│                                                         │
│  Experience Interval:                                   │
│  [ 2 ▼] to [ 3 ▼]  years                              │
│                                                         │
│  Job Titles:                                            │
│  [Software Engineer ×] [Python Developer ×] [+ Add]    │
│                                                         │
│  Skill Set:                                             │
│  [Database Design ×] [Development ×] [+ Add]           │
│                                                         │
│  ── Scheduling ──                                      │
│                                                         │
│  Feed Visit Interval:                                   │
│  [ 1 ▼] hour(s)   (options: 1, 2, 4, 6, 8, 12, 24)   │
│                                                         │
│                              [Cancel]  [Create Job]     │
└─────────────────────────────────────────────────────────┘
```

#### Page 3: Scored Posts Results (`FeedScrollResultsPage.jsx`)

```
┌─────────────────────────────────────────────────────────┐
│  ← Back to Jobs     Backend Job Hunt — Latest Scan      │
│  Scanned: 2026-08-03 14:25 UTC | Next: 15:25 UTC       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │ #1  Score: 92/100  🔥                           │   │
│  │ ─────────────────────────────────                │   │
│  │ Author: Jane Smith                               │   │
│  │ "We're hiring Senior Software Engineers!         │   │
│  │  Looking for 2-3 years experience in             │   │
│  │  Python, database design and development..."     │   │
│  │                                                  │   │
│  │ Matched: Software Engineer ✓, 2-3 years ✓,      │   │
│  │          database design ✓, development ✓        │   │
│  │                                                  │   │
│  │ [View on LinkedIn ↗]                             │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │ #2  Score: 78/100                                │   │
│  │ ─────────────────────────────────                │   │
│  │ Author: John Doe                                 │   │
│  │ "Join our team as a Python Developer..."         │   │
│  │ ...                                              │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ... (up to 10 posts, scrollable) ...                  │
│                                                         │
│  ── Scan History ──                                    │
│  [2026-08-03 14:25] [2026-08-03 13:25] [2026-08-03 12:25] │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 7.3 New Components

| Component | File | Purpose |
|-----------|------|---------|
| `FeedScrollJobCard` | `components/feed/FeedScrollJobCard.jsx` | Card for each job in the list |
| `FeedScrollJobForm` | `components/feed/FeedScrollJobForm.jsx` | Create/edit form with mode toggle |
| `ScoredPostCard` | `components/feed/ScoredPostCard.jsx` | Individual scored post display |
| `TagInput` | `components/feed/TagInput.jsx` | Reusable tag/chip input for titles & skills |
| `ScoreBadge` | `components/feed/ScoreBadge.jsx` | Color-coded score indicator (green/yellow/red) |

### 7.4 Navigation Update

Add a new entry to the sidebar in `DashboardLayout.jsx`:

```js
{
  to: '/app/feed-scroll',
  label: 'Feed Scroll',
  icon: <scroll/feed icon>,
}
```

---

## 8. File Structure Summary

### Backend (New Files)

```
models/
├── feed_scroll_job.py          ← New model
├── feed_scroll_result.py       ← New model

schemas/
├── feed_scroll.py              ← Pydantic schemas

api/v1/
├── feed_scroll.py              ← API endpoints

automation/actions/
├── feed_scroll.py              ← Feed scrolling + post extraction

automation/scoring/
├── __init__.py
├── feed_scorer.py              ← Regex scoring engine
├── base.py                     ← ScorerInterface protocol

worker/tasks/
├── feed_scroll_tasks.py        ← Celery tasks

migrations/versions/
├── <new>_add_feed_scroll_tables.py  ← Alembic migration
```

### Frontend (New Files)

```
frontend/src/
├── pages/
│   ├── FeedScrollJobsPage.jsx      ← Job list
│   ├── FeedScrollCreatePage.jsx    ← Create form
│   └── FeedScrollResultsPage.jsx   ← Scored posts view
├── components/feed/
│   ├── FeedScrollJobCard.jsx
│   ├── FeedScrollJobForm.jsx
│   ├── ScoredPostCard.jsx
│   ├── TagInput.jsx
│   └── ScoreBadge.jsx
```

---

## 9. Implementation Phases

### Phase 1 — Foundation (MVP)
1. **DB models** + Alembic migration for `feed_scroll_jobs` and `feed_scroll_results`
2. **Regex scoring engine** (`automation/scoring/feed_scorer.py`)
3. **Feed scroll action** (`automation/actions/feed_scroll.py`) — navigate, scroll, extract posts
4. **Celery task** (`worker/tasks/feed_scroll_tasks.py`) — orchestrate scan + score + store
5. **API endpoints** (`api/v1/feed_scroll.py`) — CRUD for jobs, fetch results
6. **Frontend — Create page** — form with Job Search / Post Search mode toggle
7. **Frontend — Results page** — scrollable list of 10 scored posts

### Phase 2 — Polish
8. **Frontend — Jobs list page** — overview of all jobs with status
9. **Manual scan trigger** — button to run a scan immediately
10. **Scan history** — browse previous scan results
11. **Pause/Resume** — toggle job status without deleting
12. **Sidebar navigation** update

### Phase 3 — AI Upgrade (Future)
13. **AI scorer** implementing `ScorerInterface` — swap regex for LLM-based scoring
14. **Smarter extraction** — semantic understanding of job posts vs. casual posts
15. **Notification system** — alert when a high-score post is found

---

## 10. Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scoring architecture | Interface/Protocol pattern | Easy to swap regex → AI later without touching the rest of the flow |
| Scheduling | Self-rescheduling tasks | Each job has its own interval; mirrors existing campaign_tasks pattern |
| Post deduplication | `post_urn` field | LinkedIn's URN is unique per post; prevents duplicate storage across scans |
| Score range | 0–100 | Familiar, easy to understand, room for AI to use same scale |
| Posts per scan | Top 10 (configurable) | Matches user requirement; stored per batch for history |
| Browser reuse | Existing `launch_persistent_browser` | Reuses the battle-tested stealth browser with account-pinned fingerprints |
| Anti-detection | Uses existing `human_scroll` | Feed scrolling looks like normal browsing; no new detection surface |

---

## 11. Dependencies & Reuse

This plan **maximizes reuse** of existing infrastructure:

| Existing Component | How It's Reused |
|-------------------|-----------------|
| `automation/browser.py` | `launch_persistent_browser()` — same stealth browser |
| `automation/human.py` | `human_scroll()`, `random_idle_pause()`, `human_mouse_move()` |
| `automation/session.py` | `verify_session()` — same session check before actions |
| `worker/celery_app.py` | Same Celery infrastructure |
| `worker/playwright_semaphore.py` | Same concurrency control |
| `worker/profile_lock.py` | Same per-account locking |
| `models/linkedin_account.py` | Same account model (FK target) |
| `database.py` / `Base` | Same SQLAlchemy base |

---

## 12. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| LinkedIn feed DOM changes | Use multiple fallback selectors; screenshot on failure for debugging |
| Post text extraction incomplete | Store raw HTML as fallback; improve extraction iteratively |
| High scan frequency → account risk | Enforce minimum interval (1 hour); add jitter; respect daily limits |
| Regex scoring too crude | Phase 3 AI upgrade; meantime, user can tune keywords |
| Feed never loads / auth wall | Session verification before every scan; alert user if session expires |
