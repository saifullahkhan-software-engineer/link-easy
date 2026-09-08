import api from './client';

/* ------------------------------ ai assistant ------------------------------- */
// The in-app AI assistant. Every request carries the page the widget is on
// (`current_path`) so the backend prompt knows where the user is standing —
// the backend resolves it against its app guide, never trusting it beyond a
// route shape. Provider keys never reach this bundle: the backend owns the
// AI provider call.

export const assistantApi = {
  // One turn. `conversationId` continues a conversation; omit to start one.
  chat: (message, { currentPath = null, conversationId = null } = {}) =>
    api.post(
      '/assistant/chat',
      {
        message,
        ...(currentPath ? { current_path: currentPath } : {}),
        ...(conversationId ? { conversation_id: conversationId } : {}),
      },
      { timeout: 90_000 } // the model may fan out into channel reads
    ),

  // The caller's conversations, most recent first.
  conversations: () => api.get('/assistant/conversations'),

  // One conversation's visible turns (user + assistant).
  history: (conversationId) => api.get(`/assistant/conversations/${conversationId}`),

  delete: (conversationId) => api.delete(`/assistant/conversations/${conversationId}`),
};
