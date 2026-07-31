/**
 * Headless smoke test: render the production bundle in jsdom against several
 * routes and assert key content mounts without throwing. Authenticated routes
 * are exercised against a per-case stub API server on the same jsdom origin.
 * Run: node smoke-test.mjs   (requires `npm run build` first)
 */
import { JSDOM } from 'jsdom';
import { readdirSync } from 'node:fs';
import { createServer } from 'node:http';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const assetsDir = join(root, 'dist', 'assets');
const bundleName = readdirSync(assetsDir).find((f) => f.startsWith('index-') && f.endsWith('.js'));
if (!bundleName) throw new Error('dist bundle not found — run `npm run build` first');

const AUTH_TOKENS = {
  'le.access_token': 'fake-access-token',
  'le.refresh_token': 'fake-refresh-token',
  'le.user_email': 'owner@test.dev',
  'le.user_name': 'Test Owner',
};

const ACTIVE_ACCOUNT = {
  owner_email: 'owner@test.dev',
  linkedin_email: 'li@test.dev',
  label: 'Work account',
  status: 'active',
  created_at: '2026-07-01T10:00:00Z',
  updated_at: '2026-07-20T10:00:00Z',
};

const CAMPAIGN = {
  id: 'camp-1',
  account_email: 'li@test.dev',
  name: 'Q3 Founders',
  description: 'SaaS founders outreach',
  status: 'draft',
  search_filters: null,
  daily_connection_limit: 15,
  daily_message_limit: 20,
  daily_visit_limit: 80,
  connection_note_template: 'Hi {{first_name}}',
  message_templates: ['Thanks for connecting!'],
  created_at: '2026-07-10T10:00:00Z',
  updated_at: '2026-07-10T10:00:00Z',
  started_at: null,
};

const STEPS = [
  { id: 'step-1', campaign_id: 'camp-1', step_order: 1, step_type: 'visit_profile', delay_hours: 0, condition: null },
  { id: 'step-2', campaign_id: 'camp-1', step_order: 2, step_type: 'send_connection', delay_hours: 24, condition: null },
  { id: 'step-3', campaign_id: 'camp-1', step_order: 3, step_type: 'send_message', delay_hours: 48, condition: 'accepted' },
];

const LEADS = [
  {
    id: 'lead-1',
    campaign_id: 'camp-1',
    linkedin_url: 'https://www.linkedin.com/in/janedoe',
    first_name: 'Jane',
    last_name: 'Doe',
    headline: 'VP Sales at Acme',
    status: 'pending',
    current_step: 0,
    connection_sent_at: null,
    accepted_at: null,
    last_action_at: null,
    next_action_at: null,
    notes: null,
    created_at: '2026-07-11T10:00:00Z',
  },
  {
    id: 'lead-2',
    campaign_id: 'camp-1',
    linkedin_url: 'https://www.linkedin.com/in/johnsmith',
    first_name: 'John',
    last_name: 'Smith',
    headline: null,
    status: 'replied',
    current_step: 3,
    connection_sent_at: '2026-07-12T10:00:00Z',
    accepted_at: '2026-07-13T10:00:00Z',
    last_action_at: '2026-07-14T10:00:00Z',
    next_action_at: '2026-07-29T10:00:00Z',
    notes: null,
    created_at: '2026-07-11T10:00:00Z',
  },
];

const json = (res, code, body) => {
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
};

const CASES = [
  {
    path: '/',
    mustContain: ['warm conversations', 'Automated connection requests', 'CSV lead import'],
  },
  { path: '/login', mustContain: ['Log in', 'Forgot password?'] },
  { path: '/signup', mustContain: ['Create your account', 'At least 8 characters'] },
  { path: '/forgot-password', mustContain: ['Reset your password'] },
  { path: '/app', mustContain: ['Log in'] }, // unauthenticated → redirected to /login
  {
    name: 'account page — not connected shows connect form',
    path: '/app/account',
    storage: AUTH_TOKENS,
    api: { 'GET /api/v1/linkedin/account': (res) => json(res, 404, { detail: 'Account not found' }) },
    mustContain: ['Connect your LinkedIn account', 'Connect LinkedIn account', 'LinkedIn password'],
  },
  {
    name: 'account page — active account renders card + actions',
    path: '/app/account',
    storage: AUTH_TOKENS,
    api: { 'GET /api/v1/linkedin/account': (res) => json(res, 200, ACTIVE_ACCOUNT) },
    mustContain: ['li@test.dev', 'Connected', 'Refresh session', 'Disconnect', 'Work account'],
  },
  {
    name: 'campaigns page — two-panel workspace with leads',
    path: '/app/campaigns',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/linkedin/account': (res) => json(res, 200, ACTIVE_ACCOUNT),
      'GET /api/v1/campaigns': (res) => json(res, 200, [CAMPAIGN]),
      'GET /api/v1/campaigns/camp-1/steps': (res) => json(res, 200, STEPS),
      'GET /api/v1/leads': (res) => json(res, 200, LEADS),
    },
    mustContain: ['Q3 Founders', 'Jane Doe', 'replied', 'Add manually', 'Upload CSV', 'Start campaign', 'Next step', 'Scheduled time', 'Send Message', 'Due now'],
  },
  {
    name: 'campaigns page — no account gates the page',
    path: '/app/campaigns',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/linkedin/account': (res) => json(res, 404, { detail: 'Account not found' }),
      'GET /api/v1/campaigns': (res) => json(res, 200, []),
    },
    mustContain: ['Connect a LinkedIn account first'],
  },
];

let failures = 0;

for (const testCase of CASES) {
  const { mustContain, storage = {}, api = {} } = testCase;
  const label = testCase.name || testCase.path;

  // Per-case same-origin stub API so axios('/api/v1/...') resolves to it.
  const server = createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    const key = `${req.method} ${url.pathname.replace(/\/$/, '')}`;
    const handler =
      api[key] ||
      // prefix match for path-parameter routes (e.g. /campaigns/{id}/start)
      api[Object.keys(api).find((k) => k.startsWith(`${req.method} `) && key.startsWith(k.slice(req.method.length + 1)))];
    if (handler) return handler(res, req);
    json(res, 404, { detail: `stub: no handler for ${key}` });
  });
  await new Promise((r) => server.listen(0, r));
  const port = server.address().port;

  const dom = new JSDOM('<!doctype html><html><head></head><body><div id="root"></div></body></html>', {
    url: `http://localhost:${port}${testCase.path}`,
    pretendToBeVisual: true,
    runScripts: 'outside-only',
  });

  const { window } = dom;
  window.matchMedia = (query) => ({
    matches: true, // reduced-motion → landing uses static fallback, no WebGL needed
    media: query,
    addEventListener() {},
    removeEventListener() {},
  });
  for (const [k, v] of Object.entries(storage)) window.localStorage.setItem(k, v);

  const globals = ['window', 'document', 'localStorage', 'navigator', 'HTMLElement', 'Element', 'Node', 'customElements', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'MutationObserver', 'self',
    // Browsers make axios use its XHR adapter — mirror that here so relative
    // /api/v1 URLs resolve against the jsdom origin (the stub server).
    'XMLHttpRequest', 'FormData', 'Blob', 'FileReader'];
  const saved = {};
  const setGlobal = (key, value) =>
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  for (const key of globals) {
    saved[key] = globalThis[key];
    if (window[key] !== undefined) setGlobal(key, window[key]);
  }

  const errors = [];
  window.addEventListener('error', (e) => errors.push(e.error?.message || e.message));
  const origConsoleError = console.error;
  console.error = (...args) => {
    const msg = args.map(String).join(' ');
    if (!msg.includes('not implemented') && !msg.includes('Warning:')) errors.push(msg);
  };

  try {
    await import(`./dist/assets/${bundleName}?case=${encodeURIComponent(label)}`);
    await new Promise((r) => setTimeout(r, 900)); // let effects/axios/router settle

    const html = window.document.getElementById('root')?.innerHTML || '';
    const missing = mustContain.filter((text) => !html.includes(text));
    if (missing.length || errors.length) {
      failures++;
      console.log(`✗ ${label}`);
      if (missing.length) console.log(`   missing content: ${missing.join(' | ')}`);
      if (errors.length) console.log(`   errors: ${[...new Set(errors)].slice(0, 3).join(' ;; ')}`);
    } else {
      console.log(`✓ ${label} — ${mustContain.length} assertions passed`);
    }
  } catch (err) {
    failures++;
    console.log(`✗ ${label} threw: ${err.message}`);
  } finally {
    console.error = origConsoleError;
    for (const key of globals) {
      if (saved[key] === undefined) delete globalThis[key];
      else setGlobal(key, saved[key]);
    }
    dom.window.close();
    await new Promise((r) => server.close(r));
  }
}

console.log(failures ? `\n${failures} case(s) failed` : '\nAll smoke tests passed');
process.exit(failures ? 1 : 0);
