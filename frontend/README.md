# LinkEasy — LinkedIn Automation Frontend

React 18 + Vite single-page frontend for the FastAPI LinkedIn outreach
automation backend in this repository (base path `/api/v1`).

## Stack

- **React 18 + Vite**, React Router v6
- **Tailwind CSS** (dark-first zinc/slate palette, teal accent)
- **axios** — with a single-flight JWT refresh interceptor
- **react-hot-toast** — inline feedback
- **Papaparse** — client-side CSV preview before upload

## Quick start

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api → http://localhost:8000
```

The backend must be running (see the repo root README / `docker-compose.dev.yml`).
If it runs somewhere other than `http://localhost:8000`, set `VITE_BACKEND_URL`
before `npm run dev`, or point the built app at a full API base with
`VITE_API_BASE_URL` (see `.env.example`).

## Build

```bash
npm run build      # static bundle in dist/
npm run preview    # serve the production build locally
```

For production, serve `dist/` behind a reverse proxy that also forwards
`/api/v1/*` to FastAPI, or build with `VITE_API_BASE_URL=https://your-host/api/v1`.

## Pages & flows

| Route | What it does |
| --- | --- |
| `/` | Multi-channel landing page with separate **App** and **Dashboard** entry buttons |
| `/dashboard` | Operations dashboard with automation health and service status (separate module + sidebar) |
| `/dashboard/redis-queues` | Redis, Celery queues, locks, and database job management |
| `/login` `/signup` `/verify-email` `/forgot-password` `/reset-password` | Full JWT auth lifecycle; live password-rule checklist mirrors the backend |
| `/app/*` | **App module** — sidebar contains only Account → LinkedIn → WhatsApp. Landing: `/app/account` |
| `/app/account` | Accounts hub — one card per connection (LinkedIn + WhatsApp) showing only the account and its status |
| `/app/account/linkedin` | Manage LinkedIn (Playwright login), 2FA code modal, session refresh, edit, disconnect + Scan / Live Chat shortcuts |
| `/app/account/whatsapp` | Manage WhatsApp — QR connect flow; connected card shows Added / Last updated + WhatsApp Scan / Live Chat shortcuts |
| `/admin/*` | **Admin module** — own sidebar: Accounts → Users → LinkedIn (jobs & campaign parameters) → WhatsApp (jobs & parameters) |
| `/admin/accounts` | Every LinkedIn account and WhatsApp session, with status tables |
| `/admin/users` | Users and roles |
| `/admin/linkedin` | LinkedIn campaign job audit log + campaign/job parameters |
| `/admin/whatsapp` | WhatsApp filter jobs + WhatsApp/job parameters |
| `/app/campaigns` | Two-panel campaign + leads workspace: campaign form collapses to a summary card; lead tabs (manual / CSV with Papaparse preview + per-row error list); leads table; start campaign |

## Notes on backend quirks this UI handles

- LinkedIn connect/refresh calls drive real browser automation — the UI shows
  explicit timed progress messaging and uses 150s axios timeouts for those calls.
- `POST /leads/upload` validates **all** rows before inserting any; a 422 with
  `{ message, errors: [...] }` is rendered as a scrollable per-row error list.
- Token refresh is single-flight: concurrent 401s share one
  `POST /auth/refresh` call and then replay their original requests.
