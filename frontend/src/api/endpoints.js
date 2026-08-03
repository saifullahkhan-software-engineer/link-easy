import api from './client';

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
  triggerScan: (jobId, ownerEmail) =>
    api.post(`/feed-scroll/jobs/${jobId}/scan`, null, {
      params: { owner_email: ownerEmail },
      timeout: 60_000,
    }),
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
