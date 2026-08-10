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
