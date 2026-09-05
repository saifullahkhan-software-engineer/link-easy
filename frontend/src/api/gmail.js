import api from './client';

/* ----------------------------------- gmail ---------------------------------- */
// Gmail is the app module's fourth channel: connect a personal Gmail (or
// Google Workspace) mailbox through Google OAuth, then read / check / search
// the inbox, manage labels and send messages — all through the LinkEasy
// backend, which keeps the Google tokens server-side (encrypted at rest).

export const gmailApi = {
  // Connection
  status: () => api.get('/gmail/status'),
  authUrl: () => api.get('/gmail/auth-url'),
  disconnect: () => api.delete('/gmail/connection'),

  // Mailbox
  profile: () => api.get('/gmail/profile'),
  labels: () => api.get('/gmail/labels'),
  listMessages: (params = {}) => api.get('/gmail/messages', { params }),
  unread: () => api.get('/gmail/unread'),
  getThread: (threadId) => api.get(`/gmail/threads/${threadId}`),
  getMessage: (messageId) => api.get(`/gmail/messages/${messageId}`),

  // Actions (label ids: INBOX, UNREAD, STARRED, TRASH, custom labels, ...)
  modify: (messageId, payload) => api.patch(`/gmail/messages/${messageId}`, payload),
  trash: (messageId) => api.post(`/gmail/messages/${messageId}/trash`),
  untrash: (messageId) => api.post(`/gmail/messages/${messageId}/untrash`),

  // Send
  send: (payload) => api.post('/gmail/send', payload),

  // Attachment download (the backend proxies Gmail so the browser never sees
  // the OAuth token). Returns the raw response — the caller saves the blob.
  downloadAttachment: (messageId, attachmentId) =>
    api.get(`/gmail/messages/${messageId}/attachments/${attachmentId}`, {
      responseType: 'blob',
      timeout: 60_000,
    }),
};

export default gmailApi;
