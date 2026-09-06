import api from './client';

// Tokens stay on the server. All browser traffic uses the shared same-origin
// client (including session renewal); no direct requests to Meta are made.
export const inboxApi = {
  conversations: (channel, { after, signal } = {}) =>
    api.get(`/inbox/${channel}/conversations`, { params: { after }, signal, timeout: 60_000 }),
  messages: (channel, conversationId, { signal } = {}) =>
    api.get(`/inbox/${channel}/messages`, { params: { conversation_id: conversationId }, signal, timeout: 60_000 }),
  reply: (channel, conversationId, text) =>
    api.post(`/inbox/${channel}/messages`, { conversation_id: conversationId, text }, { timeout: 90_000 }),
};
