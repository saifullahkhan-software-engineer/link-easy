import api from './client';

/* ----------------------------------- gmail ---------------------------------- */
// Gmail is the app module's fourth channel: connect a personal Gmail (or
// Google Workspace) mailbox through Google OAuth, then read / check / search
// the inbox, manage labels and send messages — all through the LinkEasy
// backend, which keeps the Google tokens server-side (encrypted at rest).

export const gmailApi = {
  // Connection — `accountId` selects one of several connected mailboxes
  // (the connection row id); omit it for the first-connected mailbox.
  status: (accountId = null) =>
    api.get('/gmail/status', { params: accountId ? { account_id: accountId } : {} }),
  authUrl: (accountId = null) =>
    api.get('/gmail/auth-url', { params: accountId ? { account_id: accountId } : {} }),
  disconnect: (accountId = null) =>
    api.delete('/gmail/connection', { params: accountId ? { account_id: accountId } : {} }),

  // Mailbox
  profile: (accountId = null) =>
    api.get('/gmail/profile', { params: accountId ? { account_id: accountId } : {} }),
  labels: (accountId = null) =>
    api.get('/gmail/labels', { params: accountId ? { account_id: accountId } : {} }),
  listMessages: (params = {}, accountId = null) =>
    api.get('/gmail/messages', {
      params: { ...(accountId ? { account_id: accountId } : {}), ...params },
    }),
  unread: (accountId = null) =>
    api.get('/gmail/unread', { params: accountId ? { account_id: accountId } : {} }),
  getThread: (threadId, accountId = null) =>
    api.get(`/gmail/threads/${threadId}`, { params: accountId ? { account_id: accountId } : {} }),
  getMessage: (messageId, accountId = null) =>
    api.get(`/gmail/messages/${messageId}`, { params: accountId ? { account_id: accountId } : {} }),

  // Actions (label ids: INBOX, UNREAD, STARRED, TRASH, custom labels, ...)
  modify: (messageId, payload, accountId = null) =>
    api.patch(`/gmail/messages/${messageId}`, payload, {
      params: accountId ? { account_id: accountId } : {},
    }),
  trash: (messageId, accountId = null) =>
    api.post(`/gmail/messages/${messageId}/trash`, null, {
      params: accountId ? { account_id: accountId } : {},
    }),
  untrash: (messageId, accountId = null) =>
    api.post(`/gmail/messages/${messageId}/untrash`, null, {
      params: accountId ? { account_id: accountId } : {},
    }),

  // Send
  send: (payload, accountId = null) =>
    api.post('/gmail/send', payload, { params: accountId ? { account_id: accountId } : {} }),

  // Attachment download (the backend proxies Gmail so the browser never sees
  // the OAuth token). Returns the raw response — the caller saves the blob.
  downloadAttachment: (messageId, attachmentId, accountId = null) =>
    api.get(`/gmail/messages/${messageId}/attachments/${attachmentId}`, {
      params: accountId ? { account_id: accountId } : {},
      responseType: 'blob',
      timeout: 60_000,
    }),
};

export default gmailApi;
