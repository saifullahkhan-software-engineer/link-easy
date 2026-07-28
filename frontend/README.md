# ReachPilot — LinkedIn Automation Frontend

React 18 + Vite single-page frontend for the FastAPI LinkedIn outreach
automation backend in this repository (base path `/api/v1`).

## Stack

- **React 18 + Vite**, React Router v6
- **Tailwind CSS** (dark-first zinc/slate palette, teal accent)
- **react-three-fiber + three** — 3D network hero on the landing page only
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
| `/` | Landing page with 3D node-network hero (reduced-motion fallback) |
| `/login` `/signup` `/verify-email` `/forgot-password` `/reset-password` | Full JWT auth lifecycle; live password-rule checklist mirrors the backend |
| `/app/account` | Connect LinkedIn (Playwright login), 2FA code modal, session refresh, edit, disconnect |
| `/app/campaigns` | Two-panel campaign + leads workspace: campaign form collapses to a summary card; lead tabs (manual / CSV with Papaparse preview + per-row error list); leads table; start campaign |

## Notes on backend quirks this UI handles

- LinkedIn connect/refresh calls drive real browser automation — the UI shows
  explicit timed progress messaging and uses 150s axios timeouts for those calls.
- `POST /leads/upload` validates **all** rows before inserting any; a 422 with
  `{ message, errors: [...] }` is rendered as a scrollable per-row error list.
- Token refresh is single-flight: concurrent 401s share one
  `POST /auth/refresh` call and then replay their original requests.
