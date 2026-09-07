import api from './client';

// Tokens stay on the server. All browser traffic uses the shared same-origin
// client (including session renewal); no direct requests to Meta are made.
//
// Multi-account: a user may have several connected accounts per inbox channel
// (e.g. a personal and a work Instagram). `accountId` selects which one the
// read/reply routes operate on; omit it for the first-connected account. The
// channel's account dropdown is populated from `accounts(channel)`.
export const inboxApi = {
  // The caller's connected accounts for one inbox channel — feeds the dropdown.
  accounts: (channel) => api.get('/inbox/accounts', { params: { channel }, timeout: 60_000 }),
  conversations: (channel, { after, signal, accountId } = {}) =>
    api.get(`/inbox/${channel}/conversations`, {
      params: { ...(accountId ? { account_id: accountId } : {}), after },
      signal,
      timeout: 60_000,
    }),
  messages: (channel, conversationId, { signal, accountId } = {}) =>
    api.get(`/inbox/${channel}/messages`, {
      params: { ...(accountId ? { account_id: accountId } : {}), conversation_id: conversationId },
      signal,
      timeout: 60_000,
    }),
  reply: (channel, conversationId, text, accountId = null) =>
    api.post(
      `/inbox/${channel}/messages`,
      { conversation_id: conversationId, text },
      { params: accountId ? { account_id: accountId } : {}, timeout: 90_000 },
    ),
};
