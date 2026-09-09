/**
 * Assistant → inbox draft handoff.
 *
 * When the user asks the assistant to message someone ("send Sara on
 * Instagram that I'll be late"), the backend records an `open_chat` action.
 * The widget stores the payload here and navigates to the inbox page, which
 * consumes it: opens the right chat, types the draft into the message box,
 * and lets the user review before anything is sent.
 */

export const COMPOSE_KEY = 'le.assistant.compose';
export const COMPOSE_MAX_AGE_MS = 10 * 60 * 1000; // stale drafts are ignored

export function readAssistantCompose() {
  try {
    const raw = sessionStorage.getItem(COMPOSE_KEY);
    if (!raw) return null;
    const payload = JSON.parse(raw);
    if (!payload || typeof payload !== 'object') return null;
    if (payload.ts && Date.now() - payload.ts > COMPOSE_MAX_AGE_MS) {
      sessionStorage.removeItem(COMPOSE_KEY);
      return null;
    }
    return {
      channel: payload.channel || null,
      conversationId: payload.conversationId || null,
      conversationName: payload.conversationName || '',
      draft: payload.draft || '',
    };
  } catch {
    return null;
  }
}

export function consumeAssistantCompose() {
  try {
    sessionStorage.removeItem(COMPOSE_KEY);
  } catch {}
}

/** Case-insensitive match of a compose target against loaded chats. */
export function matchComposeChat(chats, { conversationId, conversationName }) {
  if (!Array.isArray(chats) || !chats.length) return null;
  if (conversationId) {
    const byId = chats.find((c) => String(c.id ?? c.chat_id) === String(conversationId));
    if (byId) return byId;
  }
  const wanted = String(conversationName || '').trim().toLowerCase();
  if (wanted) {
    return (
      chats.find((c) => String(c.name || '').trim().toLowerCase() === wanted) ||
      chats.find((c) => String(c.name || '').toLowerCase().includes(wanted)) ||
      null
    );
  }
  return null;
}
