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

/**
 * Build a structurally valid (unsigned) JWT. The frontend only *decodes* the
 * token to decide what to render — the backend verifies the signature — so a
 * fake signature is exactly what a UI-level test needs.
 */
const makeToken = (payload) => {
  const b64 = (obj) =>
    Buffer.from(JSON.stringify(obj))
      .toString('base64')
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '');
  return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64(payload)}.sig`;
};

const AUTH_TOKENS = {
  'le.access_token': makeToken({ sub: 'owner@test.dev', role: 'customer', roles: ['customer'] }),
  'le.refresh_token': 'fake-refresh-token',
  'le.user_email': 'owner@test.dev',
  'le.user_name': 'Test Owner',
};

// Same session, but the token carries both roles.
const ADMIN_TOKENS = {
  ...AUTH_TOKENS,
  'le.access_token': makeToken({
    sub: 'owner@test.dev',
    role: 'admin',
    roles: ['admin', 'customer'],
  }),
};

const secondsNow = () => Math.floor(Date.now() / 1000);

// A session whose access token expired an hour ago — what the app finds in
// localStorage when a page is refreshed a while after logging in.
const EXPIRED_SESSION = {
  'le.access_token': makeToken({
    sub: 'owner@test.dev',
    role: 'customer',
    roles: ['customer'],
    token_type: 'access',
    exp: secondsNow() - 3600,
  }),
  'le.refresh_token': 'stale-refresh-token',
  'le.user_email': 'owner@test.dev',
  'le.user_name': 'Test Owner',
};

// Access token is still valid, but the fixed login-session deadline has
// passed. The browser must log out even if no API request is made.
const ABSOLUTE_SESSION_EXPIRED = {
  'le.access_token': makeToken({
    sub: 'owner@test.dev',
    role: 'customer',
    roles: ['customer'],
    token_type: 'access',
    exp: secondsNow() + 3600,
    session_expires_at: secondsNow() - 1,
  }),
  'le.refresh_token': 'stale-refresh-token',
  'le.user_email': 'owner@test.dev',
  'le.user_name': 'Test Owner',
};

const ADMIN_ME = { email: 'owner@test.dev', roles: ['admin', 'customer'], is_admin: true, admin_api_enforced: true };
const CUSTOMER_ME = { email: 'owner@test.dev', roles: ['customer'], is_admin: false, admin_api_enforced: true };

const ADMIN_OVERVIEW = {
  users: { total: 4, verified: 3, unverified: 1, admins: 1 },
  accounts: { linkedin_total: 2, linkedin_by_status: { active: 2 }, whatsapp_total: 1, whatsapp_connected: 1 },
  jobs: { by_status: { done: 7, queued: 2 }, total: 9, last_24h: 3, campaigns_by_status: { active: 1 }, campaigns_total: 1 },
  rate_limits: { active_windows_last_hour: 2, counters_with_traffic_24h: 1, enabled: true },
  generated_at: '2026-08-20T09:00:00Z',
};

const ADMIN_USERS = {
  count: 2,
  users: [
    { email: 'dev@example.com', first_name: 'Dev', last_name: 'Owner', is_verified: true, roles: ['admin', 'customer'], primary_role: 'admin', linkedin_accounts: 1, campaigns: 1, created_at: '2026-07-01T10:00:00Z' },
    { email: 'sara@example.com', first_name: 'Sara', last_name: 'Ahmed', is_verified: true, roles: ['customer'], primary_role: 'customer', linkedin_accounts: 0, campaigns: 0, created_at: '2026-07-02T10:00:00Z' },
  ],
};

const ADMIN_SETTINGS = {
  settings: [
    { key: 'campaign.daily_connection_limit', value: 15, default: 15, value_type: 'int', category: 'campaign', description: 'Connection requests per account per day', minimum: 0, maximum: 15 },
    { key: 'jobs.max_concurrent_browsers', value: 2, default: 2, value_type: 'int', category: 'jobs', description: 'Concurrent browser sessions', minimum: 1, maximum: 10 },
    { key: 'whatsapp.max_monitored_groups', value: 3, default: 3, value_type: 'int', category: 'whatsapp', description: 'Groups monitored per WhatsApp filter job', minimum: 1, maximum: 3 },
    { key: 'whatsapp.forward_delay_seconds', value: 10, default: 10, value_type: 'float', category: 'whatsapp', description: 'Pause between WhatsApp forwards (anti-block)', minimum: 1, maximum: 300 },
  ],
};

const ADMIN_RATE_LIMITS = {
  enabled: true,
  rules: {},
  counters: [
    { identity: 'ip:203.0.113.9', bucket: 'auth:login', request_count: 4, window_started_at: '2026-08-20T09:00:00Z' },
  ],
};

const ADMIN_API_STUBS = {
  'GET /api/v1/admin/me': (res) => json(res, 200, ADMIN_ME),
  'GET /api/v1/admin/overview': (res) => json(res, 200, ADMIN_OVERVIEW),
  'GET /api/v1/admin/users': (res) => json(res, 200, ADMIN_USERS),
  'GET /api/v1/admin/settings': (res) => json(res, 200, ADMIN_SETTINGS),
  'GET /api/v1/admin/rate-limits': (res) => json(res, 200, ADMIN_RATE_LIMITS),
  'GET /api/v1/admin/accounts': (res) =>
    json(res, 200, {
      counts: { linkedin_total: 2, linkedin_active: 1, whatsapp_total: 1, whatsapp_connected: 1 },
      linkedin: [
        { id: 'li-1', owner_email: 'dev@example.com', linkedin_email: 'li@test.dev', label: 'Work account', status: 'active', created_at: '2026-07-01T10:00:00Z', updated_at: '2026-07-20T10:00:00Z' },
        { id: 'li-2', owner_email: 'sara@example.com', linkedin_email: 'sara@linkedin.dev', label: null, status: 'pending_verification', created_at: '2026-08-01T10:00:00Z', updated_at: '2026-08-01T10:00:00Z' },
      ],
      whatsapp: [
        { id: 3, status: 'connected', is_active: true, created_at: '2026-07-15T09:00:00Z', updated_at: '2026-08-10T09:00:00Z' },
      ],
    }),
  'GET /api/v1/admin/jobs/linkedin': (res) =>
    json(res, 200, {
      count: 2,
      jobs: [
        { id: 'job-aaa111', campaign_id: 'camp-1', campaign_name: 'Q3 Founders', step_type: 'send_connection', status: 'done', action_message: 'Connection sent', error_message: null, scheduled_at: '2026-08-10T08:00:00Z', started_at: '2026-08-10T08:01:00Z', completed_at: '2026-08-10T08:02:00Z', created_at: '2026-08-10T08:00:00Z' },
        { id: 'job-bbb222', campaign_id: 'camp-2', campaign_name: null, step_type: 'visit_profile', status: 'queued', action_message: null, error_message: null, scheduled_at: '2026-08-11T08:00:00Z', started_at: null, completed_at: null, created_at: '2026-08-11T08:00:00Z' },
      ],
    }),
  'GET /api/v1/admin/jobs/whatsapp': (res) =>
    json(res, 200, {
      count: 1,
      jobs: [
        { id: 1, name: 'Dubai Engineering Jobs', status: 'active', role: 'Engineer', job_title: 'Backend Developer', keywords: ['python'], interval_hours: 1, next_scan_at: '2026-08-20T10:00:00Z', last_scan_at: '2026-08-20T09:00:00Z', created_at: '2026-08-01T10:00:00Z', updated_at: '2026-08-05T08:00:00Z', total_count: 9, matched_count: 2, rejected_count: 3, forwarded_count: 1 },
      ],
    }),
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

let linkedinMessageRequestCount = 0;

// Social scheduler fixtures: one pending post (in this month, in the future so it
// is "upcoming"), one published, one failed; YouTube connected, TikTok
// configured-but-not-connected, Instagram not configured on the instance.
const socialFuture = new Date(Date.now() + 2 * 24 * 3600 * 1000).toISOString();
const socialPast = new Date(Date.now() - 3 * 24 * 3600 * 1000).toISOString();
// The user's saved Facebook Groups, and what the per-post checklist shows.
const SAVED_GROUPS = [
  { id: 'st-1', platform: 'facebook', name: 'Lahore Freelancers', url: 'https://www.facebook.com/groups/lahore', created_at: socialPast },
  { id: 'st-2', platform: 'facebook', name: 'Video Editors PK', url: 'https://www.facebook.com/groups/editors', created_at: socialPast },
];
const POST_FACEBOOK_GROUPS = [{ name: 'Lahore Freelancers', url: 'https://www.facebook.com/groups/lahore' }];

const SOCIAL_POSTS = [
  {
    id: 'sp-1', title: 'Launch teaser', caption: 'We are live', hashtags: '#launch',
    video_url: 'http://x/uploads/social/a.mp4', thumbnail: '', platforms: ['youtube', 'tiktok'],
    scheduled_at: socialFuture, status: 'pending', youtube_title: '', instagram_caption: '', tiktok_caption: '',
    created_at: socialPast, updated_at: socialPast, results: [],
  },
  {
    id: 'sp-2', title: 'Old success', caption: '', hashtags: '',
    video_url: 'http://x/uploads/social/b.mp4', thumbnail: '', platforms: ['youtube'],
    scheduled_at: socialPast, status: 'posted', youtube_title: '', instagram_caption: '', tiktok_caption: '',
    created_at: socialPast, updated_at: socialPast,
    results: [{ id: 'r-1', platform: 'youtube', status: 'posted', platform_id: 'abc123', platform_url: 'https://www.youtube.com/shorts/abc123', error: '', note: 'Added to 2 playlists', posted_at: socialPast, updated_at: socialPast }],
  },
  {
    id: 'sp-3', title: 'Broken clip', caption: '', hashtags: '',
    video_url: 'http://x/uploads/social/c.mp4', thumbnail: '', platforms: ['tiktok'],
    scheduled_at: socialPast, status: 'failed', youtube_title: '', instagram_caption: '', tiktok_caption: '',
    created_at: socialPast, updated_at: socialPast,
    results: [{ id: 'r-2', platform: 'tiktok', status: 'failed', platform_id: '', platform_url: '', error: 'TikTok is not connected. Open Settings and connect the account.', posted_at: null, updated_at: socialPast }],
  },
  // YouTube published the Short but one playlist was gone: the post is marked
  // failed by the other platform, and the YouTube row still has to show what
  // did happen — that is what `note` is for.
  {
    id: 'sp-5', title: 'Group shoutout', caption: 'Join the launch', hashtags: '#launch',
    video_url: 'http://x/uploads/social/e.mp4', thumbnail: '', platforms: ['facebook'],
    scheduled_at: socialPast, status: 'posted', youtube_title: '', instagram_caption: '', tiktok_caption: '',
    created_at: socialPast, updated_at: socialPast, facebook_groups: POST_FACEBOOK_GROUPS,
    results: [{ id: 'r-5', platform: 'facebook', status: 'posted', platform_id: 'vid-5', platform_url: 'https://facebook.com/watch/?v=vid-5', error: '', note: 'Published to your Facebook Page. Share it manually to 1 group from this post\u2019s checklist.', posted_at: socialPast, updated_at: socialPast }],
  },
  {
    id: 'sp-4', title: 'Half posted', caption: '', hashtags: '',
    video_url: 'http://x/uploads/social/d.mp4', thumbnail: '', platforms: ['youtube', 'tiktok'],
    scheduled_at: socialPast, status: 'failed', youtube_title: '', instagram_caption: '', tiktok_caption: '',
    created_at: socialPast, updated_at: socialPast,
    results: [
      { id: 'r-3', platform: 'youtube', status: 'posted', platform_id: 'def456', platform_url: 'https://www.youtube.com/shorts/def456', error: '', note: 'Published, and added to 1 of 2 playlists. YouTube Shorts could not update: PLold: Playlist not found.', posted_at: socialPast, updated_at: socialPast },
      { id: 'r-4', platform: 'tiktok', status: 'failed', platform_id: '', platform_url: '', error: 'TikTok upload was rejected', posted_at: null, updated_at: socialPast },
    ],
  },
];
// The message a user pastes into the upload page, and the per-platform copy
// the backend's Groq extraction (POST /social-scheduler/parse-copy) returns
// for it. TikTok and Facebook are absent from the paste, so they come back
// empty — the AI must not invent copy for them.
const PASTED_PLATFORM_COPY = [
  '1. YouTube Shorts',
  'Title: Day 17 — Dictionaries in Python \u{1F40D}',
  'Description: Learn how dicts work in 60 seconds.',
  '#Shorts #Python #Coding',
  '',
  '2. Instagram Reels',
  'Headline (Caption Hook): Stop writing loops like it was 2010',
  'Caption: Dictionaries are faster. Here is why.',
  '#Python #Coding',
].join('\n');

const PARSED_PLATFORM_COPY = {
  youtube: {
    title: 'Day 17 — Dictionaries in Python \u{1F40D}',
    description: 'Learn how dicts work in 60 seconds.',
    hashtags: '#Shorts #Python #Coding',
  },
  instagram: {
    title: 'Stop writing loops like it was 2010',
    description: 'Dictionaries are faster. Here is why.',
    hashtags: '#Python #Coding',
  },
  tiktok: { title: '', description: '', hashtags: '' },
  facebook: { title: '', description: '', hashtags: '' },
};

let parseCopyRequestCount = 0;

// What GET /platforms/youtube/playlists returns for the connected channel.
const YOUTUBE_PLAYLISTS = [
  { id: 'PLabc', title: 'Morning Routine', privacy: 'public', item_count: 12 },
  { id: 'PLxyz', title: 'Behind the scenes', privacy: 'unlisted', item_count: 1 },
];

const SOCIAL_API_STUBS = {
  'GET /api/v1/social-scheduler/posts': (res, req) => {
    const url = new URL(req.url, 'http://x');
    const wanted = (url.searchParams.get('status') || '').split(',').filter(Boolean);
    json(res, 200, wanted.length ? SOCIAL_POSTS.filter((p) => wanted.includes(p.status)) : SOCIAL_POSTS);
  },
  'GET /api/v1/social-scheduler/stats': (res) =>
    json(res, 200, {
      scheduled_this_week: 1, total_scheduled: 1, total_published: 1, total_failed: 1,
      next_post_at: socialFuture, next_post_in: 'in 1 day 23 hours', connected_platforms: ['youtube'],
      per_platform: { youtube: { posted: 1, failed: 0 }, instagram: { posted: 0, failed: 0 }, tiktok: { posted: 0, failed: 1 } },
    }),
  // The upload editor's "Add to YouTube playlists" picker.
  'GET /api/v1/social-scheduler/platforms/youtube/playlists': (res) =>
    json(res, 200, { channel: 'My Channel', playlists: YOUTUBE_PLAYLISTS }),
  // Saved Facebook Group destinations. Meta closed the Groups API, so these
  // are a manual checklist — the stub never "posts" anywhere.
  'GET /api/v1/social-scheduler/share-targets': (res) => json(res, 200, SAVED_GROUPS),
  // Exact keys: the harness's prefix fallback compares a method-prefixed
  // request key against a bare path, so parameterised routes need one entry
  // per id a case actually touches.
  'DELETE /api/v1/social-scheduler/share-targets/st-1': (res) => {
    const index = SAVED_GROUPS.findIndex((g) => g.id === 'st-1');
    if (index === -1) return json(res, 404, { detail: 'Share target not found' });
    const [removed] = SAVED_GROUPS.splice(index, 1);
    return json(res, 200, { id: 'st-1', message: 'Share target removed', removed: removed.name });
  },
  'POST /api/v1/social-scheduler/share-targets': (res, req) => {
    let raw = '';
    req.on('data', (chunk) => { raw += chunk; });
    req.on('end', () => {
      const body = JSON.parse(raw || '{}');
      // The backend rejects anything that is not an http(s) link, because the
      // checklist renders these URLs as anchors.
      if (!/^https?:\/\//i.test(body.url || '')) {
        return json(res, 422, { detail: [{ msg: 'A group link must start with http:// or https://' }] });
      }
      const existing = SAVED_GROUPS.find((g) => g.url === body.url);
      if (existing) return json(res, 201, existing);
      const created = { id: `st-${SAVED_GROUPS.length + 1}`, platform: 'facebook', name: body.name, url: body.url, created_at: socialPast };
      SAVED_GROUPS.push(created);
      return json(res, 201, created);
    });
  },
  'GET /api/v1/social-scheduler/platforms': (res) =>
    json(res, 200, [
      { platform: 'youtube', label: 'YouTube Shorts', connected: true, configured: true, account_name: 'My Channel', account_id: 'UC1', expires_at: socialFuture, reconnect_required: false, connected_at: socialPast, updated_at: socialPast },
      { platform: 'instagram', label: 'Instagram Reels', connected: false, configured: false, account_name: '', account_id: '', expires_at: null, reconnect_required: false, connected_at: null, updated_at: null },
      { platform: 'tiktok', label: 'TikTok', connected: false, configured: true, account_name: '', account_id: '', expires_at: null, reconnect_required: false, connected_at: null, updated_at: null },
    ]),
};

const CASES = [
  {
    path: '/',
    mustContain: ['Connect your tools.', 'Automate your day.', 'Connect your social tools', 'Build campaigns in minutes'],
  },
  {
    name: 'landing (admin token) — shows BOTH App Dashboard and Admin Dashboard',
    path: '/',
    storage: ADMIN_TOKENS,
    api: { 'GET /api/v1/admin/me': (res) => json(res, 200, ADMIN_ME) },
    mustContain: ['App Dashboard', 'Admin Dashboard'],
  },
  {
    name: 'landing (customer token) — App Dashboard only, no Admin button',
    path: '/',
    storage: AUTH_TOKENS,
    api: { 'GET /api/v1/admin/me': (res) => json(res, 200, CUSTOMER_ME) },
    mustContain: ['App Dashboard'],
    mustNotContain: ['Admin Dashboard'],
  },
  {
    name: 'admin accounts — every LinkedIn account and WhatsApp session renders',
    path: '/admin',
    storage: ADMIN_TOKENS,
    api: ADMIN_API_STUBS,
    mustContain: [
      'Accounts',
      'LinkedIn accounts',
      'WhatsApp sessions',
      'li@test.dev',
      'sara@linkedin.dev',
      'LinkedIn accounts by status',
    ],
  },
  {
    name: 'admin users — users and roles table renders',
    path: '/admin/users',
    storage: ADMIN_TOKENS,
    api: ADMIN_API_STUBS,
    mustContain: ['Users', 'Users and roles', 'sara@example.com', 'role-toggle'],
  },
  {
    name: 'admin linkedin — jobs audit log and campaign parameters render',
    path: '/admin/linkedin',
    storage: ADMIN_TOKENS,
    api: ADMIN_API_STUBS,
    mustContain: [
      'LinkedIn — Jobs',
      'LinkedIn jobs (campaign audit log)',
      'Q3 Founders',
      'send_connection',
      'Campaign parameters and job limits',
      'daily_connection_limit',
      'max_concurrent_browsers',
    ],
  },
  {
    name: 'admin whatsapp — filter jobs and whatsapp parameters render',
    path: '/admin/whatsapp',
    storage: ADMIN_TOKENS,
    api: ADMIN_API_STUBS,
    mustContain: [
      'WhatsApp — Jobs',
      'WhatsApp jobs (filter jobs)',
      'Dubai Engineering Jobs',
      'WhatsApp parameters and job limits',
      'max_monitored_groups',
      'forward_delay_seconds',
    ],
  },
  {
    name: 'admin dashboard — a customer is redirected away from /admin',
    path: '/admin',
    storage: AUTH_TOKENS,
    api: {
      ...ADMIN_API_STUBS,
      'GET /api/v1/admin/me': (res) => json(res, 200, CUSTOMER_ME),
      'GET /api/v1/linkedin/account': (res) => json(res, 404, { detail: 'Account not found' }),
      'GET /api/v1/whatsapp/status': (res) => json(res, 200, { status: 'disconnected', is_active: false }),
    },
    // Redirected to /app/account, so the accounts hub renders instead.
    mustContain: ['Accounts'],
    mustNotContain: ['Users and roles', 'Rate limits (database-backed)'],
  },
  { path: '/login', mustContain: ['Log in', 'Forgot password?'] },
  { path: '/signup', mustContain: ['Create your account', 'At least 8 characters'] },
  { path: '/forgot-password', mustContain: ['Reset your password'] },
  { path: '/app', mustContain: ['Log in'] }, // unauthenticated → redirected to /login
  {
    name: 'page refresh — expired access token is renewed silently, no errors, page renders',
    path: '/app/account',
    storage: EXPIRED_SESSION,
    api: {
      'POST /api/v1/auth/refresh': (res) =>
        json(res, 200, {
          access_token: makeToken({
            sub: 'owner@test.dev',
            role: 'customer',
            roles: ['customer'],
            token_type: 'access',
            exp: secondsNow() + 3600,
          }),
          refresh_token: 'fresh-refresh-token',
          token_type: 'bearer',
        }),
      'GET /api/v1/linkedin/account': (res) => json(res, 404, { detail: 'Account not found' }),
      'GET /api/v1/whatsapp/status': (res) => json(res, 200, { status: 'disconnected', is_active: false }),
    },
    mustContain: ['Accounts', 'LinkedIn', 'WhatsApp', 'Connect LinkedIn account', 'Not connected'],
    mustNotContain: ['Session expired', 'Forgot password?'],
  },
  {
    name: 'page refresh — invalid session shows the login popup and lands on /login',
    path: '/app/account',
    storage: EXPIRED_SESSION,
    api: {
      'POST /api/v1/auth/refresh': (res) => json(res, 401, { detail: 'Invalid refresh token' }),
    },
    mustContain: ['Session expired', 'Please log in again', 'Log in', 'Forgot password?'],
  },
  {
    name: 'absolute two-hour session deadline — browser logs out without an API request',
    path: '/app/account',
    storage: ABSOLUTE_SESSION_EXPIRED,
    api: {},
    mustContain: ['Session expired', 'Please log in again', 'Log in', 'Forgot password?'],
  },
  {
    name: 'mid-session unrefreshable 401 — login popup and redirect to /login',
    path: '/app/account',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/linkedin/account': (res) => json(res, 401, { detail: 'Could not validate credentials' }),
      'GET /api/v1/whatsapp/status': (res) => json(res, 200, { status: 'disconnected', is_active: false }),
      'POST /api/v1/auth/refresh': (res) => json(res, 401, { detail: 'Invalid refresh token' }),
    },
    mustContain: ['Session expired', 'Please log in again', 'Log in', 'Forgot password?'],
    mustNotContain: ['Connect LinkedIn account'],
  },
  {
    name: 'accounts hub — two cards (LinkedIn + WhatsApp) with connect actions',
    path: '/app/account',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/linkedin/account': (res) => json(res, 404, { detail: 'Account not found' }),
      'GET /api/v1/whatsapp/status': (res) => json(res, 200, { status: 'disconnected', is_active: false }),
    },
    mustContain: ['Accounts', 'LinkedIn', 'WhatsApp', 'Connect LinkedIn account', 'Connect WhatsApp', 'Not connected'],
  },
  {
    name: 'accounts hub — connected statuses render manage actions',
    path: '/app/account',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/linkedin/account': (res) => json(res, 200, ACTIVE_ACCOUNT),
      'GET /api/v1/whatsapp/status': (res) => json(res, 200, { status: 'connected', is_active: true, created_at: '2026-07-15T09:00:00Z', updated_at: '2026-08-10T09:00:00Z' }),
    },
    // The hub only shows the account + its status; details live on the
    // manage pages.
    mustContain: ['li@test.dev', 'Manage LinkedIn account', 'Manage WhatsApp connection', 'Connected'],
    mustNotContain: ['Open scanner', 'Disconnect WhatsApp'],
  },
  {
    name: 'whatsapp connect page — disconnected shows connect flow + browser view',
    path: '/app/account/whatsapp',
    storage: AUTH_TOKENS,
    api: { 'GET /api/v1/whatsapp/status': (res) => json(res, 200, { status: 'disconnected', is_active: false }) },
    mustContain: ['WhatsApp Account', 'Connection status', 'Connect WhatsApp', 'Live Browser View'],
  },
  {
    name: 'whatsapp connect 500 stays on the page with the backend error',
    path: '/app/account/whatsapp',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/whatsapp/status': (res) => json(res, 200, { status: 'disconnected', is_active: false }),
      'POST /api/v1/whatsapp/connect': (res) =>
        json(res, 500, { detail: 'Could not start Chromium for the WhatsApp browser view.' }),
      'POST /api/v1/auth/refresh': (res) =>
        json(res, 200, {
          access_token: makeToken({
            sub: 'owner@test.dev',
            role: 'customer',
            roles: ['customer'],
            token_type: 'access',
            exp: secondsNow() + 3600,
          }),
          refresh_token: 'fresh-refresh-token',
          token_type: 'bearer',
        }),
    },
    interact: async (window) => {
      const waitFor = async (selector) => {
        for (let attempt = 0; attempt < 50; attempt += 1) {
          const element = window.document.querySelector(selector);
          if (element) return element;
          await new Promise((resolve) => setTimeout(resolve, 20));
        }
        throw new Error(`Timed out waiting for ${selector}`);
      };
      (await waitFor('[data-testid="whatsapp-connect"]')).click();
    },
    mustContain: ['Could not start Chromium', 'WhatsApp Account', 'Connect WhatsApp'],
    mustNotContain: ['Forgot password?', 'Session expired'],
  },
  {
    name: 'whatsapp connect success starts the QR waiting state',
    path: '/app/account/whatsapp',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/whatsapp/status': (res) => json(res, 200, { status: 'disconnected', is_active: false }),
      'POST /api/v1/whatsapp/connect': (res) =>
        json(res, 200, { status: 'waiting_qr', message: 'Scan the WhatsApp Web QR code to connect.' }),
    },
    interact: async (window) => {
      const waitFor = async (selector) => {
        for (let attempt = 0; attempt < 50; attempt += 1) {
          const element = window.document.querySelector(selector);
          if (element) return element;
          await new Promise((resolve) => setTimeout(resolve, 20));
        }
        throw new Error(`Timed out waiting for ${selector}`);
      };
      (await waitFor('[data-testid="whatsapp-connect"]')).click();
    },
    mustContain: ['Waiting for QR', 'WhatsApp Account', 'scan the WhatsApp Web QR code'],
    mustNotContain: ['Forgot password?', 'Session expired'],
  },
  {
    name: 'whatsapp connect page — connected account shows manage card with dates, scan and live chat',
    path: '/app/account/whatsapp',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/whatsapp/status': (res) =>
        json(res, 200, {
          status: 'connected',
          is_active: true,
          created_at: '2026-07-15T09:00:00Z',
          updated_at: '2026-08-10T09:00:00Z',
        }),
    },
    mustContain: [
      'WhatsApp Account',
      'Added',
      'Jul 15, 2026',
      'Status details',
      'connected',
      'WhatsApp Scan',
      'Live Chat',
      'jobs need to be stopped before using Live Chat',
      'Disconnect WhatsApp',
    ],
  },
  {
    name: 'whatsapp filters — list page replaces the scanner landing page',
    path: '/app/whatsapp-scanner',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/whatsapp/filters/jobs': (res) => json(res, 200, []),
    },
    mustContain: ['WhatsApp Filters', 'New Filter', 'No WhatsApp filters yet', 'Create Filter'],
  },
  {
    name: 'whatsapp live chat — running session shows the top ten conversation list',
    path: '/app/whatsapp-live',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/whatsapp/live/status': (res) => json(res, 200, {
        status: 'running', message: 'Live chat is open. The scanner is paused.',
        error: null, active_chat_id: null, active_chat_name: null,
      }),
      'GET /api/v1/whatsapp/live/chats': (res, req) => {
        const url = new URL(req.url, 'http://x');
        if (url.searchParams.get('limit') !== '10') {
          return json(res, 400, { detail: 'expected the top-ten chat limit' });
        }
        return json(res, 200, {
          chats: [
            { chat_id: 'chat-1', name: 'Customer Support', preview: 'Can we talk today?', unread_count: 2 },
            { chat_id: 'chat-2', name: 'Ava Patel', preview: 'Thanks!', unread_count: 0 },
          ],
          count: 2,
          query: null,
        });
      },
    },
    mustContain: ['WhatsApp Live Chat', 'Browsing chats', '10 most recent chats', 'Customer Support', 'Ava Patel', 'Pick a chat to start'],
  },
  {
    name: 'linkedin live chat — running session renders stable conversation rows',
    path: '/app/linkedin-live',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/linkedin/live/status': (res) => json(res, 200, {
        status: 'running', message: 'LinkedIn live chat is running.',
        error: null, active_chat_id: null, active_chat_name: null,
      }),
      'GET /api/v1/linkedin/live/chats': (res) => json(res, 200, {
        chats: [
          { chat_id: 'thread-1', name: 'Sam Founder', preview: 'Thanks for reaching out', unread_count: 1 },
          { chat_id: 'thread-2', name: 'Amina Recruiter', preview: 'Would Tuesday work?', unread_count: 0 },
        ],
        count: 2,
      }),
      'POST /api/v1/linkedin/live/chats/open': (res, req) => {
        let body = '';
        req.on('data', (chunk) => { body += chunk; });
        req.on('end', () => {
          const { chat_id: chatId } = JSON.parse(body);
          const names = { 'thread-1': 'Sam Founder', 'thread-2': 'Amina Recruiter' };
          if (!names[chatId]) return json(res, 400, { detail: 'unexpected chat id' });
          return json(res, 200, {
            ok: true, chat_id: chatId, name: names[chatId], error: null,
          });
        });
      },
      'POST /api/v1/linkedin/live/chats/close': (res) => json(res, 200, {
        ok: true, chat_id: null, name: null, error: null,
      }),
      'GET /api/v1/linkedin/live/messages': (res) => {
        linkedinMessageRequestCount += 1;
        if (linkedinMessageRequestCount === 1) {
          // Deliberately resolve Sam's request after Amina's. The first result
          // must not overwrite the newly selected conversation.
          setTimeout(() => json(res, 200, {
            chat_id: 'thread-1',
            messages: [{ message_id: 'stale-1', text: 'STALE SAM MESSAGE', is_outgoing: false, timestamp: '3:40 PM' }],
            count: 1,
          }), 450);
          return;
        }
        json(res, 200, {
          chat_id: 'thread-2',
          messages: [{ message_id: 'fresh-1', text: 'Fresh message from Amina', is_outgoing: false, timestamp: '3:41 PM' }],
          count: 1,
        });
      },
    },
    interact: async (window) => {
      linkedinMessageRequestCount = 0;
      const waitFor = async (selector) => {
        for (let attempt = 0; attempt < 50; attempt += 1) {
          const element = window.document.querySelector(selector);
          if (element) return element;
          await new Promise((resolve) => setTimeout(resolve, 20));
        }
        throw new Error(`Timed out waiting for ${selector}`);
      };
      (await waitFor('[data-testid="linkedin-chat-row-thread-1"]')).click();
      const back = await waitFor('[data-testid="linkedin-chat-back"]');
      for (let attempt = 0; attempt < 50 && linkedinMessageRequestCount === 0; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      back.click();
      (await waitFor('[data-testid="linkedin-chat-row-thread-2"]')).click();
    },
    mustContain: ['LinkedIn Live Chat', 'Amina Recruiter', 'Fresh message from Amina'],
    mustNotContain: ['STALE SAM MESSAGE'],
  },
  {
    name: 'linkedin profile scan — renders report and PDF before download',
    path: '/app/linkedin-profile',
    storage: AUTH_TOKENS,
    api: {
      'POST /api/v1/linkedin/profile/scan': (res) => json(res, 200, {
        report: {
          basics: { name: 'Ada Lovelace', headline: 'Computing pioneer', location: 'London' },
          about: 'Built foundational ideas for programmable machines.',
          experience: [{ title: 'Mathematician', company: 'Analytical Engine', dates: '1842–1852' }],
          education: [{ school: 'Private study', degree: 'Mathematics', dates: '1830–1835' }],
          skills: ['Mathematics', 'Algorithms'],
          source_url: 'https://www.linkedin.com/in/ada-lovelace/',
        },
        filename: 'ada-lovelace-scan.pdf',
        pdf_base64: Buffer.from('%PDF-1.4 smoke preview').toString('base64'),
      }),
    },
    interact: async (window) => {
      const input = window.document.querySelector('[data-testid="linkedin-profile-url"]');
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, 'https://www.linkedin.com/in/ada-lovelace/');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
      input.dispatchEvent(new window.Event('change', { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 50));
      window.document.querySelector('[data-testid="linkedin-profile-scan"]').click();
    },
    mustContain: [
      'Scan preview', 'Ada Lovelace', 'Computing pioneer',
      'Built foundational ideas', 'Generated PDF preview', 'Download PDF',
    ],
  },
  {
    name: 'whatsapp filter detail — read-only summary, checkpoints, stats, and messages',
    path: '/app/whatsapp-scanner/jobs/1',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/whatsapp/status': (res) => json(res, 200, { status: 'connected', is_active: true }),
      'GET /api/v1/whatsapp/filters/jobs/1': (res) => json(res, 200, {
        id: 1, name: 'Dubai Engineering Jobs', status: 'active', role: 'Engineer',
        job_title: 'Backend Developer', keywords: ['python'], experience_level: 'senior',
        match_threshold: 60, interval_hours: 1, latest_messages_limit: 25,
        monitored_group_names: ['Dubai Jobs'],
        monitored_groups: [{
          id: 7, group_name: 'Dubai Jobs', whatsapp_id: 'g1',
          last_checked_at: '2026-08-10T10:00:00Z', last_message_id: 'wamid.latest-123',
          last_message_timestamp: '10:00',
        }],
        forward_group_name: 'Matched Jobs',
        forward_group: { id: 8, group_name: 'Matched Jobs', whatsapp_id: 'g2' },
        total_count: 9, matched_count: 2, rejected_count: 3, forwarded_count: 1,
      }),
      'GET /api/v1/whatsapp/stats': (res) => json(res, 200, {
        matched_count: 2, rejected_count: 3, forwarded_count: 1, pending_count: 4, total_count: 9,
      }),
      'GET /api/v1/whatsapp/messages': (res) => json(res, 200, { messages: [], total: 0, page: 1, page_size: 20 }),
    },
    mustContain: ['Connected', 'Dubai Engineering Jobs', 'Configuration Summary', 'Scan Checkpoints', 'wamid.latest-123', 'Edit Filter', 'Trigger Manual Scan'],
    mustNotContain: ['Select Groups to Monitor', 'Search Filters'],
  },
  {
    name: 'whatsapp filter edit — criteria, one-to-three groups, and latest-message limit',
    path: '/app/whatsapp-scanner/jobs/1/edit',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/whatsapp/status': (res) => json(res, 200, { status: 'connected', is_active: true }),
      'GET /api/v1/whatsapp/filters/jobs/1': (res) => json(res, 200, {
        id: 1, name: 'Dubai Engineering Jobs', status: 'draft', role: 'Engineer',
        job_title: 'Backend Developer', keywords: ['python'], experience_level: 'senior',
        match_threshold: 60, interval_hours: 1, latest_messages_limit: 25,
        monitored_group_names: ['Dubai Jobs'],
        monitored_groups: [{ id: 7, group_name: 'Dubai Jobs', whatsapp_id: 'g1' }],
        forward_group_name: 'Matched Jobs',
        forward_group: { id: 8, group_name: 'Matched Jobs', whatsapp_id: 'g2' },
      }),
      'GET /api/v1/whatsapp/groups': (res) => json(res, 200, {
        groups: [
          { group_name: 'Dubai Jobs', whatsapp_id: 'g1' },
          { group_name: 'Matched Jobs', whatsapp_id: 'g2' },
        ],
        monitored_group_names: ['Dubai Jobs'],
        forward_group_name: 'Matched Jobs',
      }),
    },
    mustContain: ['Edit WhatsApp Filter', 'Search Filters', 'Select Groups to Monitor', 'Choose between 1 and 3 groups', 'Latest Messages / Group', 'Incremental scanning is enabled automatically'],
  },
  {
    name: 'linkedin account page — not connected shows connect form',
    path: '/app/account/linkedin',
    storage: AUTH_TOKENS,
    api: { 'GET /api/v1/linkedin/account': (res) => json(res, 404, { detail: 'Account not found' }) },
    mustContain: ['Connect your LinkedIn account', 'Connect LinkedIn account', 'LinkedIn password'],
  },
  {
    name: 'linkedin connect 400 stays on the page with an actionable error',
    path: '/app/account/linkedin',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/linkedin/account': (res) => json(res, 404, { detail: 'Account not found' }),
      'POST /api/v1/linkedin/account': (res) =>
        json(res, 400, { detail: 'LinkedIn rejected the sign-in: Wrong email or password.' }),
      'POST /api/v1/auth/refresh': (res) =>
        json(res, 200, {
          access_token: makeToken({
            sub: 'owner@test.dev',
            role: 'customer',
            roles: ['customer'],
            token_type: 'access',
            exp: secondsNow() + 3600,
          }),
          refresh_token: 'fresh-refresh-token',
          token_type: 'bearer',
        }),
    },
    interact: async (window) => {
      const waitFor = async (selector) => {
        for (let attempt = 0; attempt < 50; attempt += 1) {
          const element = window.document.querySelector(selector);
          if (element) return element;
          await new Promise((resolve) => setTimeout(resolve, 20));
        }
        throw new Error(`Timed out waiting for ${selector}`);
      };
      const setValue = (input, value) => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(input, value);
        input.dispatchEvent(new window.Event('input', { bubbles: true }));
        input.dispatchEvent(new window.Event('change', { bubbles: true }));
      };
      setValue(await waitFor('#li-email'), 'li@test.dev');
      setValue(await waitFor('#li-password'), 'not-the-password');
      (await waitFor('[data-testid="linkedin-connect"]')).click();
    },
    mustContain: ['Wrong email or password', 'Connect your LinkedIn account'],
    mustNotContain: ['Forgot password?', 'Session expired'],
  },
  {
    name: 'linkedin account page — active account renders card + actions',
    path: '/app/account/linkedin',
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
      'GET /api/v1/campaigns/camp-1/jobs': (res) => json(res, 200, []),
      'GET /api/v1/leads': (res) => json(res, 200, LEADS),
    },
    mustContain: ['Q3 Founders', 'Jane Doe', 'replied', 'Lead Status', 'Start campaign', 'Next step', 'Scheduled time', 'Send Message', 'Due now'],
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
  {
    name: 'feed scroll create — job search exposes extra keyword criteria',
    path: '/app/feed-scroll/create',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/linkedin/account': (res) => json(res, 200, ACTIVE_ACCOUNT),
    },
    mustContain: [
      'Create Feed Scroll Job',
      'Job Search Configuration',
      'Keywords / Extra Terms',
      'Add terms that make the job search more precise',
      'Feed Visit Interval',
    ],
  },
  {
    name: 'feed scroll results — post card shows name, profile url, post link, feed metadata',
    path: '/app/feed-scroll/jobs/feed-job-1',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/feed-scroll/jobs/feed-job-1': (res) =>
        json(res, 200, {
          id: 'feed-job-1',
          account_email: 'li@test.dev',
          owner_email: 'owner@test.dev',
          name: 'Backend job hunt',
          mode: 'job_search',
          status: 'active',
          experience_min_years: 2,
          experience_max_years: 5,
          job_titles: ['Software Engineer'],
          skill_set: ['Python'],
          keywords: ['remote'],
          feed_interval_hours: 1,
          posts_per_scan: 20,
          last_scanned_at: '2026-08-05T08:00:00Z',
          next_scan_at: '2026-08-05T09:00:00Z',
          created_at: '2026-08-01T10:00:00Z',
          updated_at: '2026-08-05T08:00:00Z',
        }),
      'GET /api/v1/feed-leads/pools': (res) =>
        json(res, 200, [
          { feed_scroll_job_id: 'feed-job-1', name: 'Backend job hunt', mode: 'job_search', status: 'active', saved_count: 0, imported_count: 0, last_saved_at: null },
        ]),
      'GET /api/v1/feed-leads': (res) => json(res, 200, []),
      'GET /api/v1/feed-scroll/jobs/feed-job-1/results': (res) =>
        json(res, 200, [
          {
            id: 'res-1',
            feed_scroll_job_id: 'feed-job-1',
            post_urn: 'urn:li:activity:7123456789012345678',
            post_url: 'https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678/',
            author_name: 'Jane Doe',
            author_first_name: 'Jane',
            author_last_name: 'Doe',
            author_profile_url: 'https://www.linkedin.com/in/janedoe',
            connection_degree: '1st',
            post_time: '5d',
            post_text: 'Excited to share that we shipped a big feature today!',
            score: 82,
            matched_terms: ['Software Engineer', 'Python'],
            scan_batch_id: 'batch-1',
            scanned_at: '2026-08-05T08:00:00Z',
            created_at: '2026-08-05T08:00:00Z',
          },
        ]),
    },
    mustContain: [
      'Backend job hunt',
      'remote',
      'Jane',
      'Doe',
      'linkedin.com/in/janedoe',
      'From your feed',
      '1st connection',
      '5d',
      'we shipped a big feature today',
      'Post link',
      'Open post',
      'Add to Lead',
    ],
  },
  {
    name: 'feed scroll results — profile already saved shows the Added state, not an addable button',
    path: '/app/feed-scroll/jobs/feed-job-1',
    storage: AUTH_TOKENS,
    api: {
      'GET /api/v1/feed-scroll/jobs/feed-job-1': (res) =>
        json(res, 200, {
          id: 'feed-job-1',
          account_email: 'li@test.dev',
          owner_email: 'owner@test.dev',
          name: 'Backend job hunt',
          mode: 'job_search',
          status: 'active',
          experience_min_years: null,
          experience_max_years: null,
          job_titles: ['Software Engineer'],
          skill_set: [],
          keywords: ['remote'],
          feed_interval_hours: 1,
          posts_per_scan: 20,
          last_scanned_at: '2026-08-05T08:00:00Z',
          next_scan_at: null,
          created_at: '2026-08-01T10:00:00Z',
          updated_at: '2026-08-05T08:00:00Z',
        }),
      'GET /api/v1/feed-leads/pools': (res) =>
        json(res, 200, [
          { feed_scroll_job_id: 'feed-job-1', name: 'Backend job hunt', mode: 'job_search', status: 'active', saved_count: 1, imported_count: 0, last_saved_at: '2026-08-05T09:00:00Z' },
        ]),
      // status=saved and status=imported hit the same stub; only the saved
      // profile is returned, which is what the card state is derived from.
      'GET /api/v1/feed-leads': (res, req) =>
        json(res, 200, new URL(req.url, 'http://x').searchParams.get('status') === 'saved'
          ? [{
              id: 'fl-1',
              owner_email: 'owner@test.dev',
              feed_scroll_job_id: 'feed-job-1',
              feed_scroll_result_id: 'res-1',
              linkedin_url: 'https://www.linkedin.com/in/janedoe',
              first_name: 'Jane',
              last_name: 'Doe',
              headline: null,
              label: null,
              source: 'job_feed_scan',
              source_post_url: 'https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678/',
              matched_score: 82,
              matched_criteria: ['Software Engineer'],
              scan_id: 'batch-1',
              status: 'saved',
              imported_campaign_id: null,
              imported_lead_id: null,
              imported_at: null,
              created_at: '2026-08-05T09:00:00Z',
            }]
          : []),
      'GET /api/v1/feed-scroll/jobs/feed-job-1/results': (res) =>
        json(res, 200, [
          {
            id: 'res-1',
            feed_scroll_job_id: 'feed-job-1',
            post_urn: 'urn:li:activity:7123456789012345678',
            post_url: 'https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678/',
            author_name: 'Jane Doe',
            author_first_name: 'Jane',
            author_last_name: 'Doe',
            author_profile_url: 'https://www.linkedin.com/in/janedoe',
            connection_degree: '1st',
            post_time: '5d',
            post_text: 'Excited to share that we shipped a big feature today!',
            score: 82,
            matched_terms: ['Software Engineer'],
            scan_batch_id: 'batch-1',
            scanned_at: '2026-08-05T08:00:00Z',
            created_at: '2026-08-05T08:00:00Z',
          },
        ]),
    },
    mustContain: [
      'Added ✓',
      'waiting in this scan',
      'add them to a campaign',
    ],
  },

  /* ───────────── social scheduler (YouTube / Instagram / TikTok) ───────────── */
  {
    name: 'social scheduler — overview renders stats, next post and the connect nudge',
    path: '/app/social-scheduler',
    storage: AUTH_TOKENS,
    api: SOCIAL_API_STUBS,
    mustContain: [
      'Social Scheduler', 'Overview', 'Schedule Post', 'Launch teaser', 'Published', 'Some platforms are not connected',
      'Connect platforms', 'View queue',
    ],
  },
  {
    name: 'social scheduler — schedule form shows upload, platforms and per-platform copy toggle',
    path: '/app/social-scheduler/schedule',
    storage: AUTH_TOKENS,
    api: SOCIAL_API_STUBS,
    mustContain: [
      'Schedule a post', 'Drop a video here', 'YouTube Shorts', 'Instagram Reels', 'TikTok', 'My Channel',
      'Not connected', 'Customise', 'Publish at', 'video-input',
    ],
  },
  {
    name: 'social scheduler — the YouTube playlist picker lists the channel playlists and tracks the selection',
    path: '/app/social-scheduler/schedule',
    storage: AUTH_TOKENS,
    api: SOCIAL_API_STUBS,
    interact: async (window) => {
      const { document } = window;
      const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const until = async (predicate, message) => {
        for (let attempt = 0; attempt < 150; attempt += 1) {
          const value = predicate();
          if (value) return value;
          await tick(20);
        }
        throw new Error(`Timed out waiting for ${message}`);
      };
      const section = () => document.querySelector('[data-testid="youtube-playlists"]');

      // YouTube is connected, so it is pre-selected and the picker fetches.
      await until(() => document.querySelector('[data-testid="playlist-PLabc"]'), 'the channel playlists to load');
      if (!section().textContent.includes('Morning Routine')) throw new Error('playlist title missing');
      if (!section().textContent.includes('12 videos')) throw new Error('playlist size missing');
      // A non-public collection is labelled so a Short is not filed somewhere
      // the user did not mean.
      if (!section().textContent.includes('unlisted')) throw new Error('privacy label missing');

      const box = document.querySelector('[data-testid="playlist-PLabc"]');
      box.click();
      await until(() => box.checked, 'the checkbox to register the selection');
      await until(() => section().textContent.includes('1 selected'), 'the selection counter to update');

      // Un-ticking clears it again — the payload sends an empty list then.
      box.click();
      await until(() => !box.checked, 'the checkbox to clear');
      await until(() => section().textContent.includes('0 selected'), 'the counter to fall back to zero');
    },
    mustContain: ['Add to YouTube playlists', 'Morning Routine', 'Behind the scenes'],
  },
  {
    name: 'social scheduler — the Facebook group picker lists saved groups and saves a new one',
    path: '/app/social-scheduler/schedule',
    storage: AUTH_TOKENS,
    api: SOCIAL_API_STUBS,
    interact: async (window) => {
      const { document } = window;
      const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const until = async (predicate, message) => {
        for (let attempt = 0; attempt < 150; attempt += 1) {
          const value = predicate();
          if (value) return value;
          await tick(20);
        }
        throw new Error(`Timed out waiting for ${message}`);
      };
      const section = () => document.querySelector('[data-testid="facebook-groups"]');

      // Facebook is not connected in this stub, and the picker must still work:
      // nothing is posted to a group, so no OAuth connection is required.
      const facebookButton = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('Facebook Reels'));
      if (!facebookButton) throw new Error('the Facebook platform button is missing');
      facebookButton.click();

      await until(() => section() && document.querySelector('[data-testid="group-st-1"]'), 'the saved groups to load');
      if (!section().textContent.includes('Lahore Freelancers')) throw new Error('saved group name missing');
      if (!section().textContent.includes('closed its Groups API')) throw new Error('the manual-sharing explanation is missing');

      document.querySelector('[data-testid="group-st-1"]').click();
      await until(() => section().textContent.includes('1 selected'), 'the selection counter to update');

      // Adding a group inline saves it and selects it.
      const nameInput = document.querySelector('[data-testid="new-group-name"]');
      const urlInput = document.querySelector('[data-testid="new-group-url"]');
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(nameInput, 'Karachi Creators');
      nameInput.dispatchEvent(new window.Event('input', { bubbles: true }));
      setter.call(urlInput, 'https://www.facebook.com/groups/karachi');
      urlInput.dispatchEvent(new window.Event('input', { bubbles: true }));
      document.querySelector('[data-testid="add-group"]').click();

      await until(() => section().textContent.includes('Karachi Creators'), 'the new group to appear');
      await until(() => section().textContent.includes('2 selected'), 'the new group to be selected automatically');

      // A non-http link is refused before it reaches the backend.
      setter.call(urlInput, 'javascript:alert(1)');
      urlInput.dispatchEvent(new window.Event('input', { bubbles: true }));
      document.querySelector('[data-testid="add-group"]').click();
      await tick(60);
      if (!section().textContent.includes('2 selected')) throw new Error('a javascript: URL changed the selection');
    },
    mustContain: ['Share to Facebook groups', 'Add a group'],
  },
  {
    name: 'social scheduler — a playlist fetch failure explains itself and offers a retry',
    path: '/app/social-scheduler/schedule',
    storage: AUTH_TOKENS,
    // Its own stub, because the page fetches as soon as YouTube is selected —
    // the first call has to fail before any interaction can arrange it.
    api: (() => {
      let attempts = 0;
      return {
        ...SOCIAL_API_STUBS,
        'GET /api/v1/social-scheduler/platforms/youtube/playlists': (res) => {
          attempts += 1;
          if (attempts === 1) return json(res, 502, { detail: 'YouTube playlist list failed: quotaExceeded' });
          return json(res, 200, { channel: 'My Channel', playlists: YOUTUBE_PLAYLISTS });
        },
      };
    })(),
    interact: async (window) => {
      const { document } = window;
      const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const until = async (predicate, message) => {
        for (let attempt = 0; attempt < 150; attempt += 1) {
          const value = predicate();
          if (value) return value;
          await tick(20);
        }
        throw new Error(`Timed out waiting for ${message}`);
      };

      await until(
        () => document.querySelector('[data-testid="playlists-retry"]'),
        'the inline failure with a retry button',
      );
      if (!document.body.innerHTML.includes('quotaExceeded')) throw new Error('backend reason not shown');
      // The rest of the form must still work: publishing without playlists is
      // a normal outcome, not a blocked one.
      if (document.querySelector('[data-testid="video-input"]') === null) throw new Error('upload control lost');

      document.querySelector('[data-testid="playlists-retry"]').click();
      await until(() => document.querySelector('[data-testid="playlist-PLabc"]'), 'the retry to load the playlists');
    },
    mustContain: ['Add to YouTube playlists'],
  },
  {
    name: 'social scheduler — Extract with AI shows a loading state, then fills the platform fields',
    path: '/app/social-scheduler/schedule',
    storage: AUTH_TOKENS,
    api: {
      ...SOCIAL_API_STUBS,
      'POST /api/v1/social-scheduler/parse-copy': (res, req) => {
        let body = '';
        req.on('data', (chunk) => { body += chunk; });
        req.on('end', () => {
          const { source_text: sourceText } = JSON.parse(body);
          // The backend refuses an empty paste; the page must never send one.
          if (!sourceText || !sourceText.trim()) {
            return json(res, 400, { detail: 'Paste a message to extract the platform copy from' });
          }
          parseCopyRequestCount += 1;
          // Hold the reply so the button's loading state is observable.
          setTimeout(() => json(res, 200, { platform_copy: PARSED_PLATFORM_COPY }), 300);
        });
      },
    },
    interact: async (window) => {
      parseCopyRequestCount = 0;
      const { document } = window;
      const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const until = async (predicate, message) => {
        for (let attempt = 0; attempt < 150; attempt += 1) {
          const value = predicate();
          if (value) return value;
          await tick(20);
        }
        throw new Error(`Timed out waiting for ${message}`);
      };
      const extractButton = () => document.querySelector('[data-testid="ai-extract"]');
      const pasteText = (value) => {
        const area = document.querySelector('[data-testid="paste-source"]');
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
        setter.call(area, value);
        area.dispatchEvent(new window.Event('input', { bubbles: true }));
      };

      if (!extractButton().disabled) throw new Error('Extract with AI must be disabled with nothing pasted');

      pasteText(PASTED_PLATFORM_COPY);
      await until(() => !extractButton().disabled, 'the button to enable once text is pasted');
      extractButton().click();

      // Loading: the button is disabled, relabelled, and the request reaches
      // the backend — exactly once (no double submit).
      await until(
        () => (extractButton().disabled && extractButton().textContent.includes('Extracting') ? extractButton() : null),
        'the loading state while the request runs',
      );
      await until(() => (parseCopyRequestCount === 1 ? true : null), 'the parse-copy request to reach the server');

      // Settled: re-enabled, editor opened, fields filled, success toast shown.
      await until(() => !extractButton().disabled, 'the button to re-enable after the reply');
      if (parseCopyRequestCount !== 1) throw new Error(`expected 1 parse-copy request, saw ${parseCopyRequestCount}`);
      const title = await until(() => document.querySelector('#copy-youtube-title'), 'the per-platform editor to open');
      if (title.value !== PARSED_PLATFORM_COPY.youtube.title) {
        throw new Error(`YouTube title not filled: ${JSON.stringify(title.value)}`);
      }
      const hashtags = document.querySelector('#copy-youtube-hashtags');
      if (!hashtags || hashtags.value !== '#Shorts #Python #Coding') {
        throw new Error(`YouTube hashtags not filled: ${JSON.stringify(hashtags?.value)}`);
      }
      // A platform that was not in the paste stays empty — nothing invented.
      const igTitle = document.querySelector('#copy-instagram-title');
      if (igTitle && igTitle.value !== PARSED_PLATFORM_COPY.instagram.title) {
        throw new Error(`Instagram title wrong: ${JSON.stringify(igTitle.value)}`);
      }
      await until(() => document.body.innerHTML.includes('Extracted copy for YouTube Shorts'), 'the success toast');
    },
    // #root-level assertions: the editor is open and the manual fields and the
    // local fallback parser are both still there.
    mustContain: ['Extract with AI', 'Use text to fill fields', 'YouTube title', 'Internal post title'],
  },
  {
    name: 'social scheduler — a failed AI extraction toasts the error and leaves the manual editor alone',
    path: '/app/social-scheduler/schedule',
    storage: AUTH_TOKENS,
    api: {
      ...SOCIAL_API_STUBS,
      'POST /api/v1/social-scheduler/parse-copy': (res) =>
        json(res, 502, {
          detail: 'The AI service did not return usable copy. Try again, or fill the fields yourself.',
        }),
    },
    interact: async (window) => {
      const { document } = window;
      const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const until = async (predicate, message) => {
        for (let attempt = 0; attempt < 150; attempt += 1) {
          const value = predicate();
          if (value) return value;
          await tick(20);
        }
        throw new Error(`Timed out waiting for ${message}`);
      };
      const extractButton = () => document.querySelector('[data-testid="ai-extract"]');

      const area = document.querySelector('[data-testid="paste-source"]');
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
      setter.call(area, PASTED_PLATFORM_COPY);
      area.dispatchEvent(new window.Event('input', { bubbles: true }));
      await until(() => !extractButton().disabled, 'the button to enable once text is pasted');
      extractButton().click();

      // The button must not stay stuck in its loading state after a failure.
      await until(() => !extractButton().disabled, 'the button to re-enable after the failure');
      await until(
        () => (document.body.innerHTML.includes('did not return usable copy') ? true : null),
        'the error toast with the backend message',
      );
      if (document.querySelector('#copy-youtube-title')) {
        throw new Error('a failed extraction must not open or fill the per-platform editor');
      }
      // The offline fallback stays usable so the user is never stuck.
      if (document.querySelector('[data-testid="paste-fill"]').disabled) {
        throw new Error('the local parser fallback must stay available after a failure');
      }
    },
    mustContain: ['Extract with AI', 'Use text to fill fields', 'Paste your platform copy'],
    mustNotContain: ['Extracted copy for'],
  },
  {
    name: 'social scheduler — queue groups scheduled posts and failures with re-queue',
    path: '/app/social-scheduler/queue',
    storage: AUTH_TOKENS,
    api: SOCIAL_API_STUBS,
    mustContain: [
      'Queue', 'Scheduled (1)', 'Needs attention (2)', 'Launch teaser', 'Broken clip', 'Re-queue', 'not connected',
      // a partially published post: the failure AND the note about the Short
      'Half posted', 'TikTok upload was rejected', 'added to 1 of 2 playlists',
    ],
  },
  {
    name: 'social scheduler — history lists outcomes with platform links',
    path: '/app/social-scheduler/history',
    storage: AUTH_TOKENS,
    api: SOCIAL_API_STUBS,
    mustContain: [
      'History', 'Old success', 'https://www.youtube.com/shorts/abc123', '1 published', 'Broken clip', 'Failed —',
      'Added to 2 playlists',
      // the manual group checklist, with the reason it is manual
      'Share to 1 Facebook group yourself', 'Lahore Freelancers', 'Copy caption', 'closed its Groups API',
    ],
  },
  {
    name: 'social scheduler — calendar renders a month grid with the post',
    path: '/app/social-scheduler/calendar',
    storage: AUTH_TOKENS,
    api: SOCIAL_API_STUBS,
    mustContain: ['Calendar', 'Mon', 'Sun', 'calendar-day', 'Launch teaser', 'this month'],
  },
  {
    name: 'social scheduler — settings manages the saved Facebook groups',
    path: '/app/social-scheduler/settings',
    storage: AUTH_TOKENS,
    api: SOCIAL_API_STUBS,
    interact: async (window) => {
      const { document } = window;
      const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const until = async (predicate, message) => {
        for (let attempt = 0; attempt < 150; attempt += 1) {
          const value = predicate();
          if (value) return value;
          await tick(20);
        }
        throw new Error(`Timed out waiting for ${message}`);
      };
      const card = () => document.querySelector('[data-testid="saved-groups"]');
      const before = SAVED_GROUPS.length;

      await until(() => card() && card().textContent.includes('Lahore Freelancers'), 'the saved groups to load');
      // The card has to say why this is manual, or the feature reads as broken.
      if (!card().textContent.includes('removed its Groups API')) throw new Error('the API explanation is missing');

      document.querySelector('[data-testid="remove-group-st-1"]').click();
      await until(() => SAVED_GROUPS.length === before - 1, 'the group to be removed');
      await until(() => !card().textContent.includes('Lahore Freelancers'), 'the row to disappear');
      // restore the fixture for the other cases
      SAVED_GROUPS.unshift({ id: 'st-1', platform: 'facebook', name: 'Lahore Freelancers', url: 'https://www.facebook.com/groups/lahore', created_at: socialPast });
    },
    mustContain: ['Facebook groups for manual sharing', 'Video Editors PK', 'Save group'],
  },
  {
    name: 'social scheduler — settings shows connect / disconnect state per platform',
    path: '/app/social-scheduler/settings?platform=tiktok&connected=1',
    storage: AUTH_TOKENS,
    api: SOCIAL_API_STUBS,
    mustContain: [
      'Settings', 'Connected platforms: 1 of 3', 'My Channel', 'Disconnect', 'Reconnect', 'Connect TikTok',
      'Not available on this instance', 'platform-card-instagram',
    ],
  },
];

// Optional subset: `node smoke-test.mjs --filter "social scheduler"` runs only
// the cases whose name matches. Useful while working on one page, and it keeps
// a single unrelated broken case from hiding the ones you just changed.
const FILTER = (() => {
  const at = process.argv.indexOf('--filter');
  return at === -1 ? null : String(process.argv[at + 1] || '').toLowerCase();
})();

let failures = 0;
let ran = 0;
const nativeTimers = {
  setTimeout: globalThis.setTimeout,
  clearTimeout: globalThis.clearTimeout,
  setInterval: globalThis.setInterval,
  clearInterval: globalThis.clearInterval,
};

for (const testCase of CASES) {
  const { mustContain, mustNotContain = [], storage = {}, api = {}, interact } = testCase;
  const label = testCase.name || testCase.path;
  if (FILTER && !label.toLowerCase().includes(FILTER)) continue;
  ran += 1;

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
  window.URL.createObjectURL ||= () => 'blob:linkedin-profile-preview';
  window.URL.revokeObjectURL ||= () => {};
  window.HTMLElement.prototype.scrollIntoView ||= () => {};
  window.matchMedia = (query) => ({
    matches: true, // reduced-motion → landing uses static fallback, no WebGL needed
    media: query,
    addEventListener() {},
    removeEventListener() {},
  });
  // jsdom has no EventSource — the live browser view panel needs one.
  window.EventSource = class EventSource {
    constructor(url) {
      this.url = url;
      this.readyState = 0;
    }
    addEventListener() {}
    removeEventListener() {}
    close() {}
  };
  for (const [k, v] of Object.entries(storage)) window.localStorage.setItem(k, v);

  const globals = ['window', 'document', 'localStorage', 'sessionStorage', 'navigator', 'HTMLElement', 'Element', 'Node', 'customElements', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'MutationObserver', 'self',
    // Browsers make axios use its XHR adapter — mirror that here so relative
    // /api/v1 URLs resolve against the jsdom origin (the stub server).
    'XMLHttpRequest', 'FormData', 'Blob', 'FileReader', 'EventSource', 'matchMedia', 'URL'];
  const saved = {};
  const setGlobal = (key, value) =>
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  for (const key of globals) {
    saved[key] = globalThis[key];
    if (window[key] !== undefined) setGlobal(key, window[key]);
  }

  // Production bundles run as Node modules here, so their bare timer calls do
  // not belong to jsdom automatically. Track them per case to stop one live
  // chat poll leaking requests into the next case's stub API.
  const timeoutHandles = new Set();
  const intervalHandles = new Set();
  setGlobal('setTimeout', (callback, delay, ...args) => {
    let handle;
    handle = nativeTimers.setTimeout(() => {
      timeoutHandles.delete(handle);
      callback(...args);
    }, delay);
    timeoutHandles.add(handle);
    return handle;
  });
  setGlobal('clearTimeout', (handle) => {
    timeoutHandles.delete(handle);
    nativeTimers.clearTimeout(handle);
  });
  setGlobal('setInterval', (callback, delay, ...args) => {
    const handle = nativeTimers.setInterval(callback, delay, ...args);
    intervalHandles.add(handle);
    return handle;
  });
  setGlobal('clearInterval', (handle) => {
    intervalHandles.delete(handle);
    nativeTimers.clearInterval(handle);
  });

  const errors = [];
  window.addEventListener('error', (e) => errors.push(e.error?.message || e.message));
  const origConsoleError = console.error;
  console.error = (...args) => {
    const msg = args.map(String).join(' ');
    if (!msg.includes('not implemented') && !msg.includes('Warning:')) errors.push(msg);
  };

  try {
    await import(`./dist/assets/${bundleName}?case=${encodeURIComponent(label)}`);
    await new Promise((r) => setTimeout(r, interact ? 250 : 900));
    if (interact) {
      await interact(window);
      await new Promise((r) => setTimeout(r, 900));
    }

    const html = window.document.getElementById('root')?.innerHTML || '';
    const missing = mustContain.filter((text) => !html.includes(text));
    const unexpected = mustNotContain.filter((text) => html.includes(text));
    if (missing.length || unexpected.length || errors.length) {
      failures++;
      console.log(`✗ ${label}`);
      if (missing.length) console.log(`   missing content: ${missing.join(' | ')}`);
      if (unexpected.length) console.log(`   unexpected content: ${unexpected.join(' | ')}`);
      if (errors.length) console.log(`   errors: ${[...new Set(errors)].slice(0, 3).join(' ;; ')}`);
    } else {
      console.log(`✓ ${label} — ${mustContain.length + mustNotContain.length} assertions passed`);
    }
  } catch (err) {
    failures++;
    console.log(`✗ ${label} threw: ${err.message}`);
  } finally {
    console.error = origConsoleError;
    for (const handle of timeoutHandles) nativeTimers.clearTimeout(handle);
    for (const handle of intervalHandles) nativeTimers.clearInterval(handle);
    setGlobal('setTimeout', nativeTimers.setTimeout);
    setGlobal('clearTimeout', nativeTimers.clearTimeout);
    setGlobal('setInterval', nativeTimers.setInterval);
    setGlobal('clearInterval', nativeTimers.clearInterval);
    for (const key of globals) {
      if (saved[key] === undefined) delete globalThis[key];
      else setGlobal(key, saved[key]);
    }
    dom.window.close();
    await new Promise((r) => server.close(r));
  }
}

if (FILTER) {
  console.log(`\n(filter "${FILTER}" — ran ${ran} of ${CASES.length} cases)`);
}
console.log(failures ? `\n${failures} case(s) failed` : '\nAll smoke tests passed');
process.exit(failures ? 1 : 0);
