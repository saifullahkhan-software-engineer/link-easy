import api, { getAccessToken } from './client';

/* ---------------------------------- auth --------------------------------- */
export const authApi = {
  register: (payload) => api.post('/auth/register', payload),
  verifyEmail: (email, code) => api.post('/auth/verify-email', { email, code }),
  resendVerification: (email) => api.post('/auth/resend-verification', { email }),
  login: (email, password) => api.post('/auth/login', { email, password }),
  forgotPassword: (email) => api.post('/auth/forgot-password', { email }),
  resetPassword: (token, password) => api.post('/auth/reset-password', { token, password }),
};

/* ------------------------------- linkedin -------------------------------- */
// LinkedIn login/refresh calls drive real browser automation on the backend
// and can take 30-60s — give them enough headroom without letting the user
// stare at a spinner for too long when something goes wrong.
const LINKEDIN_TIMEOUT = 90_000;

export const linkedinApi = {
  connect: (payload) => api.post('/linkedin/account', payload, { timeout: LINKEDIN_TIMEOUT }),
  getAccount: () => api.get('/linkedin/account'),
  updateAccount: (payload) => api.patch('/linkedin/account', payload),
  disconnect: () => api.delete('/linkedin/account'),
  submitVerificationCode: (sessionId, code) =>
    api.post(
      '/linkedin/account/verify',
      { session_id: sessionId, verification_code: code },
      { timeout: LINKEDIN_TIMEOUT }
    ),
  verifySession: (ownerEmail) =>
    api.post('/linkedin/account/verify-session', null, {
      params: { owner_email: ownerEmail },
      timeout: LINKEDIN_TIMEOUT,
    }),
};

/* ------------------------------- campaigns ------------------------------- */
export const campaignsApi = {
  list: (ownerEmail) => api.get('/campaigns', { params: { owner_email: ownerEmail } }),
  get: (campaignId, ownerEmail) =>
    api.get(`/campaigns/${campaignId}`, { params: { owner_email: ownerEmail } }),
  create: (ownerEmail, payload) =>
    api.post('/campaigns', payload, { params: { owner_email: ownerEmail } }),
  start: (campaignId, ownerEmail) =>
    api.post(`/campaigns/${campaignId}/start`, null, {
      params: { owner_email: ownerEmail },
      timeout: 60_000,
    }),
  pause: (campaignId, ownerEmail) =>
    api.post(`/campaigns/${campaignId}/pause`, null, {
      params: { owner_email: ownerEmail },
    }),
  restart: (campaignId, ownerEmail) =>
    api.post(`/campaigns/${campaignId}/restart`, null, {
      params: { owner_email: ownerEmail },
      timeout: 60_000,
    }),
  delete: (campaignId, ownerEmail) =>
    api.delete(`/campaigns/${campaignId}`, {
      params: { owner_email: ownerEmail },
    }),
  listJobs: (campaignId, ownerEmail) =>
    api.get(`/campaigns/${campaignId}/jobs`, { params: { owner_email: ownerEmail } }),
  listSteps: (campaignId, ownerEmail) =>
    api.get(`/campaigns/${campaignId}/steps`, { params: { owner_email: ownerEmail } }),
  // Lead intake — both write the same leads table as CSV/manual import.
  quickAddLead: (campaignId, payload) =>
    api.post(`/campaigns/${campaignId}/leads/quick-add`, payload),
  importFeedLeads: (campaignId, ownerEmail, feedLeadIds) =>
    api.post(
      `/campaigns/${campaignId}/leads/import-feed-leads`,
      { owner_email: ownerEmail, feed_lead_ids: feedLeadIds },
      { timeout: 60_000 }
    ),
};

/* --------------------------------- leads --------------------------------- */
/* ------------------------------ feed scroll ------------------------------ */
export const feedScrollApi = {
  listJobs: (ownerEmail) =>
    api.get('/feed-scroll/jobs', { params: { owner_email: ownerEmail } }),
  getJob: (jobId, ownerEmail) =>
    api.get(`/feed-scroll/jobs/${jobId}`, { params: { owner_email: ownerEmail } }),
  createJob: (payload) =>
    api.post('/feed-scroll/jobs', payload),
  updateJob: (jobId, ownerEmail, payload) =>
    api.patch(`/feed-scroll/jobs/${jobId}`, payload, { params: { owner_email: ownerEmail } }),
  deleteJob: (jobId, ownerEmail) =>
    api.delete(`/feed-scroll/jobs/${jobId}`, { params: { owner_email: ownerEmail } }),
  getResults: (jobId, ownerEmail, scanBatchId) =>
    api.get(`/feed-scroll/jobs/${jobId}/results`, {
      params: { owner_email: ownerEmail, ...(scanBatchId ? { scan_batch_id: scanBatchId } : {}) },
    }),
  activateJob: (jobId, ownerEmail) =>
    api.post(`/feed-scroll/jobs/${jobId}/activate`, null, { params: { owner_email: ownerEmail } }),
  pauseJob: (jobId, ownerEmail) =>
    api.post(`/feed-scroll/jobs/${jobId}/pause`, null, { params: { owner_email: ownerEmail } }),
  // Remove a single scanned post from the results view (soft dismiss). The
  // row stays in the DB so the scanner's de-dup won't bring it back.
  deleteResult: (jobId, resultId, ownerEmail) =>
    api.delete(`/feed-scroll/jobs/${jobId}/results/${resultId}`, {
      params: { owner_email: ownerEmail },
    }),
  // Undo a dismiss — bring the post back into the results view.
  restoreResult: (jobId, resultId, ownerEmail) =>
    api.post(`/feed-scroll/jobs/${jobId}/results/${resultId}/restore`, null, {
      params: { owner_email: ownerEmail },
    }),
  triggerScan: (jobId, ownerEmail) =>
    api.post(`/feed-scroll/jobs/${jobId}/scan`, null, {
      params: { owner_email: ownerEmail },
      timeout: 60_000,
    }),
  // Mark a post as applied so it is permanently tracked and excluded from future duplicate scans
  markApplied: (jobId, resultId, ownerEmail) =>
    api.post(`/feed-scroll/jobs/${jobId}/results/${resultId}/apply`, null, {
      params: { owner_email: ownerEmail },
    }),
  createAppliedPost: (jobId, ownerEmail, payload) =>
    api.post(`/feed-scroll/jobs/${jobId}/applied-posts`, payload, {
      params: { owner_email: ownerEmail },
    }),
  listAppliedPosts: (jobId, ownerEmail) =>
    api.get(`/feed-scroll/jobs/${jobId}/applied-posts`, {
      params: { owner_email: ownerEmail },
    }),
  deleteAppliedPost: (jobId, appliedId, ownerEmail) =>
    api.delete(`/feed-scroll/jobs/${jobId}/applied-posts/${appliedId}`, {
      params: { owner_email: ownerEmail },
    }),
  bulkDeleteAppliedPosts: (jobId, ownerEmail, postIds) =>
    api.post(
      `/feed-scroll/jobs/${jobId}/applied-posts/bulk-delete`,
      { post_ids: postIds },
      { params: { owner_email: ownerEmail } }
    ),
};

/* --------------------------------- leads --------------------------------- */
export const leadsApi = {
  add: (payload) => api.post('/leads', payload),
  list: (campaignId, ownerEmail) =>
    api.get('/leads', { params: { campaign_id: campaignId, owner_email: ownerEmail } }),
  uploadCsv: (file, campaignId, ownerEmail) => {
    const form = new FormData();
    form.append('file', file);
    return api.post('/leads/upload', form, {
      params: { campaign_id: campaignId, owner_email: ownerEmail },
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60_000,
    });
  },
};

/* ------------------------------- feed leads ------------------------------ */
/* The staging pool between a Feed Scroll scan and a campaign. Saving a post's
 * author parks it in the pool of that feed scroll job; a campaign later pulls
 * a selection of them in through campaignsApi.importFeedLeads. */
export const feedLeadsApi = {
  save: (payload) => api.post('/feed-leads', payload),
  list: (ownerEmail, { feedScrollJobId, status = 'saved' } = {}) =>
    api.get('/feed-leads', {
      params: {
        owner_email: ownerEmail,
        ...(feedScrollJobId ? { feed_scroll_job_id: feedScrollJobId } : {}),
        ...(status ? { status } : {}),
      },
    }),
  pools: (ownerEmail, { onlyWithSaved = false } = {}) =>
    api.get('/feed-leads/pools', {
      params: { owner_email: ownerEmail, only_with_saved: onlyWithSaved },
    }),
  remove: (feedLeadId, ownerEmail) =>
    api.delete(`/feed-leads/${feedLeadId}`, { params: { owner_email: ownerEmail } }),
};

/* ----------------------------- whatsapp scanner ---------------------------- */
const WHATSAPP_TIMEOUT = 120_000;

export const whatsappApi = {
  connect: () =>
    api.post('/whatsapp/connect', null, { timeout: WHATSAPP_TIMEOUT }),
  disconnect: () =>
    api.delete('/whatsapp/connection', { timeout: WHATSAPP_TIMEOUT }),
  getStatus: () =>
    api.get('/whatsapp/status'),

  // The groups endpoint still talks to WhatsApp Web, but saved selections are
  // scoped to a filter job when filterId is supplied.
  getGroups: (search = '', filterId = null) =>
    api.get('/whatsapp/groups', {
      params: {
        ...(search ? { search } : {}),
        ...(filterId ? { filter_id: filterId } : {}),
      },
      timeout: WHATSAPP_TIMEOUT,
    }),
  selectGroups: (payload) =>
    api.post('/whatsapp/groups/select', payload),

  // Legacy singleton endpoints kept for older links/integrations.
  getFilters: () =>
    api.get('/whatsapp/filters'),
  saveFilters: (payload) =>
    api.post('/whatsapp/filters', payload),

  // Filter-job workflow: list -> detail -> start/pause/delete.
  listFilterJobs: () =>
    api.get('/whatsapp/filters/jobs'),
  getFilterJob: (filterId) =>
    api.get(`/whatsapp/filters/jobs/${filterId}`),
  createFilterJob: (payload) =>
    api.post('/whatsapp/filters/jobs', payload),
  updateFilterJob: (filterId, payload) =>
    api.patch(`/whatsapp/filters/jobs/${filterId}`, payload),
  deleteFilterJob: (filterId) =>
    api.delete(`/whatsapp/filters/jobs/${filterId}`),
  resetFilterMessages: (filterId) =>
    api.delete(`/whatsapp/filters/jobs/${filterId}/messages`),
  activateFilterJob: (filterId) =>
    api.post(`/whatsapp/filters/jobs/${filterId}/activate`),
  pauseFilterJob: (filterId) =>
    api.post(`/whatsapp/filters/jobs/${filterId}/pause`),

  getMessages: (params = {}, filterId = null) =>
    api.get('/whatsapp/messages', {
      params: { ...params, ...(filterId ? { filter_id: filterId } : {}) },
    }),
  triggerScan: (filterId = null) =>
    api.post(
      '/whatsapp/scan/trigger',
      null,
      {
        params: filterId ? { filter_id: filterId } : {},
        timeout: WHATSAPP_TIMEOUT,
      }
    ),
  getStats: (filterId = null) =>
    api.get('/whatsapp/stats', {
      params: filterId ? { filter_id: filterId } : {},
    }),
};

/* ----------------------------- whatsapp live chat ---------------------------- */
// Live chat mirrors the WhatsApp Web UI on top of a dedicated Playwright session.
// Polling-only transport (the user picked polling over SSE to keep the wiring
// simple and the API stateless).
//
// Hot-path endpoints warrant a longer timeout:
//   - POST /start   — launching the headless browser + warm-up (~30s)
//   - POST /stop    — tearing it down (~15s)
//   - POST /open    — searching for / clicking the chat (~15s, search-then-click)
//   - POST /send    — paced send (up to WHATSAPP_FORWARD_DELAY_SECONDS inside)
const WHATSAPP_LIVE_TIMEOUT = 60_000;
const WHATSAPP_LIVE_START_TIMEOUT = 120_000;

export const whatsappLiveApi = {
  start: () =>
    api.post('/whatsapp/live/start', null, { timeout: WHATSAPP_LIVE_START_TIMEOUT }),
  stop: () =>
    api.post('/whatsapp/live/stop', null, { timeout: WHATSAPP_LIVE_TIMEOUT }),
  getStatus: () => api.get('/whatsapp/live/status'),
  listChats: ({ q = '', limit = 10 } = {}) =>
    api.get('/whatsapp/live/chats', {
      params: { ...(q ? { q } : {}), limit },
      timeout: WHATSAPP_LIVE_TIMEOUT,
    }),
  openChat: (chatId) =>
    api.post(
      '/whatsapp/live/chats/open',
      { chat_id: chatId },
      { timeout: WHATSAPP_LIVE_TIMEOUT }
    ),
  closeChat: () =>
    api.post('/whatsapp/live/chats/close', null, {
      timeout: WHATSAPP_LIVE_TIMEOUT,
    }),
  // No special endpoint — GET /messages or POST /send handle reading/writing
  // when a chat is open. Frontend may call closeChat first, then GET /messages
  // returns 409 if no chat was open. Open chat list via /chats (Sidebar).
  getMessages: ({ limit = 50 } = {}) =>
    api.get('/whatsapp/live/messages', {
      params: { limit },
      timeout: WHATSAPP_LIVE_TIMEOUT,
    }),
  sendMessage: (text) =>
    api.post(
      '/whatsapp/live/messages/send',
      { text },
      { timeout: WHATSAPP_LIVE_TIMEOUT }
    ),
};

/* ----------------------------- linkedin live chat ------------------------ */
// Mirror of the WhatsApp live surface. The same antispam pacing logic keeps
// fast typers from triggering LinkedIn's automation filter.
const LINKEDIN_LIVE_TIMEOUT = 60_000;

export const linkedinLiveApi = {
  start:  () => api.post('/linkedin/live/start',   null, { timeout: LINKEDIN_LIVE_TIMEOUT }),
  stop:   () => api.post('/linkedin/live/stop',    null, { timeout: LINKEDIN_LIVE_TIMEOUT }),
  getStatus:    () => api.get('/linkedin/live/status'),
  listChats:    ({ q = '', limit = 30 } = {}) =>
    api.get('/linkedin/live/chats', {
      params: { ...(q ? { q } : {}), limit },
      timeout: LINKEDIN_LIVE_TIMEOUT,
    }),
  openChat:     (chatId) =>
    api.post('/linkedin/live/chats/open', { chat_id: chatId }, { timeout: LINKEDIN_LIVE_TIMEOUT }),
  closeChat:    () =>
    api.post('/linkedin/live/chats/close', null, { timeout: LINKEDIN_LIVE_TIMEOUT }),
  getMessages:  ({ limit = 50 } = {}) =>
    api.get('/linkedin/live/messages', { params: { limit } }),
  sendMessage:  (text) =>
    api.post(
      '/linkedin/live/messages/send',
      { text },
      { timeout: LINKEDIN_LIVE_TIMEOUT }
    ),
};

/* ---------------------------- linkedin profile PDF ------------------------- */
export const linkedinProfileApi = {
  scan: (profileUrl) =>
    api.post(
      '/linkedin/profile/scan',
      { profile_url: profileUrl },
      { timeout: 120_000 },
    ),
};

/* ------------------------------ system / redis queues ------------------------------ */
export const systemQueuesApi = {
  overview: () => api.get('/system/queues/overview'),
  cleanupStale: () => api.post('/system/queues/cleanup-stale', null, { timeout: 45_000 }),
  redisInfo: () => api.get('/system/queues/redis-info'),
  celeryInspect: () => api.get('/system/queues/celery-inspect'),
  dbStats: () => api.get('/system/queues/db-stats'),
  listRedisKeys: ({ pattern = '*', limit = 100, offset = 0, key_type } = {}) =>
    api.get('/system/queues/redis-keys', { params: { pattern, limit, offset, ...(key_type ? { key_type } : {}) } }),
  deleteRedisKeys: (keys) => api.post('/system/queues/redis-keys/delete', { keys }),
  flushPattern: ({ pattern, limit = 100, dry_run = false }) =>
    api.post('/system/queues/flush-pattern', { pattern, limit, dry_run }),
  purgeQueue: (queueName = 'celery') => api.post('/system/queues/purge', { queue_name: queueName }),
  clearLocks: (types = ['session', 'profile', 'semaphore'], keys = []) =>
    api.post('/system/queues/clear-locks', { types, keys }),
  clearRateLimits: ({ pattern = 'rate:*', limit = 1000, dry_run = false } = {}) =>
    api.post('/system/queues/clear-rate-limits', { pattern, limit, dry_run }),
  revokeTask: (taskId, terminate = false) => api.post('/system/queues/revoke', { task_id: taskId, terminate }),
  deleteCampaignJob: (jobId) => api.delete(`/system/queues/db/campaign-jobs/${jobId}`),
  bulkDeleteCampaignJobs: (payload) => api.post('/system/queues/db/campaign-jobs/bulk-delete', payload),
};

/* ----------------------------------- admin --------------------------------- */

export const adminApi = {
  // Available to any signed-in user — it is what tells the UI whether to
  // offer the Admin Dashboard button at all.
  me: () => api.get('/admin/me'),
  overview: () => api.get('/admin/overview'),
  listUsers: ({ q, limit = 100 } = {}) =>
    api.get('/admin/users', { params: { ...(q ? { q } : {}), limit } }),
  setUserRoles: (email, roles) => api.put(`/admin/users/${encodeURIComponent(email)}/roles`, { roles }),
  getSettings: () => api.get('/admin/settings'),
  updateSettings: (values) => api.put('/admin/settings', { values }),
  rateLimits: ({ limit = 100 } = {}) => api.get('/admin/rate-limits', { params: { limit } }),
  resetRateLimit: ({ identity, bucket } = {}) =>
    api.post('/admin/rate-limits/reset', null, {
      params: { ...(identity ? { identity } : {}), ...(bucket ? { bucket } : {}) },
    }),
  // Per-section admin views (own sidebar, separate from the app module).
  accounts: () => api.get('/admin/accounts'),
  linkedinJobs: () => api.get('/admin/jobs/linkedin'),
  whatsappJobs: () => api.get('/admin/jobs/whatsapp'),
};

/* --------------------------------- live debug ------------------------------ */
// EventSource cannot send Authorization headers, so the live streams accept
// the access token as a query parameter (?token=...) instead.
const liveStreamUrl = (path) => {
  const base = import.meta.env.VITE_API_BASE_URL || '/api/v1';
  const token = getAccessToken();
  const sep = path.includes('?') ? '&' : '?';
  return `${base}${path}${sep}token=${encodeURIComponent(token || '')}`;
};

export const liveApi = {
  logsStreamUrl: () => liveStreamUrl('/live/logs'),
  browserStreamUrl: () => liveStreamUrl('/live/browser/stream'),
  browserFrameUrl: () => liveStreamUrl('/live/browser/frame'),
  browserStatus: () => api.get('/live/browser/status'),
  browserStart: (url) => api.post('/live/browser/start', { url }),
  browserStop: () => api.post('/live/browser/stop'),
  browserInput: (payload) => api.post('/live/browser/input', payload),
};
