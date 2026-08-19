/**
 * WhatsApp Live Chat page.
 *
 * FILE: frontend/src/pages/WhatsAppLiveChatPage.jsx
 *
 * Mirrors the WhatsApp Web UI on top of the dedicated Playwright session
 * exposed by /api/v1/whatsapp/live. Two panes:
 *
 *   ┌──── Sidebar ─────┐ ┌──── Active chat ──────────┐
 *   │ start/stop       │ │ header (chat name + close)│
 *   │ filter input     │ │ scrollable messages      │
 *   │ chat list rows   │ │ message input + send     │
 *   └──────────────────┘ └──────────────────────────┘
 *
 * Transport: polling only (the user picked this over SSE). Intervals:
 *   - status: every 5s (detect external session shut-down)
 *   - chats:  every 8s (side-bar picks up new conversations while open)
 *   - messages: every 3s WHILE a chat is active
 *
 * Anti-block pacing for manual sends: the server enforces
 * ``WHATSAPP_FORWARD_DELAY_SECONDS`` between consecutive forwards via the
 * ``send_message`` API. We mirror that client-side so the input visibly
 * lapses ("Sending in 7s...") instead of appearing frozen — when the user
 * hits Send, the response carries ``throttled_seconds`` we surface as a hint.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { whatsappLiveApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';

// ── Constants ────────────────────────────────────────────────────────────────
const STATUS_POLL_MS = 5_000;
const CHATS_POLL_MS = 8_000;
const MESSAGES_POLL_MS = 3_000;
const FILTER_DEBOUNCE_MS = 300;
const DEFAULT_CHAT_LIMIT = 10;
// Mirror of the server's WHATSAPP_FORWARD_DELAY_SECONDS (configured in
// core/config.py) — used client-side so the input disables + shows a
// countdown consistent with what the user will actually experience.
const SEND_THROTTLE_SECONDS = 10;

// ── Helpers ──────────────────────────────────────────────────────────────────

function describeStatus(snap) {
  if (!snap) return { label: 'Not started', tone: 'idle' };
  if (snap.status === 'running') {
    return snap.active_chat_name
      ? { label: `Chatting with ${snap.active_chat_name}`, tone: 'active' }
      : { label: 'Browsing chats', tone: 'active' };
  }
  if (snap.status === 'starting') return { label: 'Starting…', tone: 'starting' };
  if (snap.status === 'error') return { label: snap.message || 'Error', tone: 'error' };
  return { label: snap.message || 'Stopped', tone: 'idle' };
}

function formatRelativeTime(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return null;
  if (seconds <= 0) return 'now';
  if (seconds < 60) return `in ${Math.ceil(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const secs = Math.ceil(seconds % 60);
  if (minutes < 60) return `in ${minutes}m ${secs}s`;
  return `in ${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export default function WhatsAppLiveChatPage() {
  // ── Lifecycle state ──────────────────────────────────────────────────────
  const [status, setStatus] = useState(null); // LiveBrowserManager snapshot
  const [isStarting, setIsStarting] = useState(false);
  const [isStopping, setIsStopping] = useState(false);

  // ── Chat list state ──────────────────────────────────────────────────────
  const [chats, setChats] = useState([]);
  const [filter, setFilter] = useState('');
  const [filterDebounced, setFilterDebounced] = useState('');
  const [listLoading, setListLoading] = useState(false);
  const [activeChatId, setActiveChatId] = useState(null);
  const [openingChatId, setOpeningChatId] = useState(null);

  // ── Messages + send ──────────────────────────────────────────────────────
  const [messages, setMessages] = useState([]);
  const [msgsLoading, setMsgsLoading] = useState(false);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [sendCountdown, setSendCountdown] = useState(0); // > 0 -> waiting

  // Refs for cleanup-stable callbacks + auto-scroll.
  const messagesEndRef = useRef(null);
  const lastSendTsRef = useRef(0);
  const freezeStatusRef = useRef(false);
  const filterTimerRef = useRef(null);
  const activeChatRef = useRef(null);
  const messagesRequestRef = useRef(0);
  const statusRequestRef = useRef(0);

  // Snapshot of the running state for derived views (avoids stale closures).
  const isRunning = status?.status === 'running';
  const statusInfo = useMemo(() => describeStatus(status), [status]);

  // ── Pollers ───────────────────────────────────────────────────────────────

  const refreshStatus = useCallback(async () => {
    if (freezeStatusRef.current) return; // don't clobber an in-flight action
    const requestId = ++statusRequestRef.current;
    try {
      const { data } = await whatsappLiveApi.getStatus();
      // Ignore a request that began before start/stop/open/close changed the
      // authoritative browser state.
      if (requestId !== statusRequestRef.current || freezeStatusRef.current) return;
      setStatus(data);
    } catch (err) {
      // Backend hiccup — keep the last known status visible to the user.
      // Surfacing as a toast would be noisy while they try to chat.
    }
  }, []);

  const refreshChats = useCallback(async (rawFilter, { silent = true } = {}) => {
    try {
      if (!silent) setListLoading(true);
      const { data } = await whatsappLiveApi.listChats({
        q: rawFilter || '',
        limit: DEFAULT_CHAT_LIMIT,
      });
      // The backend returns `{ chats, count, query }` — see schema.
      setChats(data.chats || []);
    } catch (err) {
      // 409 = "live not running" (transitioning to idle mid-poll) — benign,
      // and background polls must never spam toasts. Anything else on a
      // user-initiated load is a real failure: surfacing it beats rendering an
      // empty sidebar that looks like "WhatsApp has no chats".
      const status = err?.response?.status;
      if (!silent && status !== 409) {
        toast.error(getErrorMessage(err, 'Could not load your chats.'), {
          id: 'whatsapp-live-chats',
        });
      } else {
        toast.dismiss('whatsapp-live-chats');
      }
    } finally {
      if (!silent) setListLoading(false);
    }
  }, []);

  const refreshMessages = useCallback(async () => {
    if (!activeChatId) return;
    const requestedChatId = activeChatId;
    const requestId = ++messagesRequestRef.current;
    try {
      setMsgsLoading(true);
      const { data } = await whatsappLiveApi.getMessages({ limit: 50 });
      // Backend returns oldest→newest. Apply it only if the user is still on
      // the conversation that initiated this request.
      if (
        requestId === messagesRequestRef.current &&
        activeChatRef.current === requestedChatId
      ) {
        setMessages(data.messages || []);
      }
    } catch (err) {
      if (requestId !== messagesRequestRef.current) return;
      const detail = getErrorMessage(err, '');
      if (!detail.includes('No chat')) {
        // 409 "No chat is currently open" gets swallowed silently while
        // the user navigates back to the list.
        const message = detail || 'The server did not return an error description.';
        toast.error(
          message.startsWith('Could not read messages:')
            ? message
            : `Could not read messages: ${message}`,
          { id: 'whatsapp-live-msgs' },
        );
      }
    } finally {
      if (requestId === messagesRequestRef.current) setMsgsLoading(false);
    }
  }, [activeChatId]);

  // Debounce the chat filter input so we don't hammer /chats on every keystroke.
  useEffect(() => {
    if (filterTimerRef.current) clearTimeout(filterTimerRef.current);
    filterTimerRef.current = setTimeout(() => setFilterDebounced(filter), FILTER_DEBOUNCE_MS);
    return () => filterTimerRef.current && clearTimeout(filterTimerRef.current);
  }, [filter]);

  // Run a /chats fetch whenever (a) we're running and (b) the filter text
  // settled. Initial fetch plus on-mount.
  useEffect(() => {
    if (!isRunning) {
      setChats([]);
      return undefined;
    }
    // Sidebar search/filtering mutates WhatsApp's shared page. Keep the loaded
    // list visible, but do not poll it while message reads/sends own the pane.
    if (activeChatId) return undefined;
    refreshChats(filterDebounced, { silent: false });
    const id = setInterval(() => refreshChats(filterDebounced), CHATS_POLL_MS);
    return () => clearInterval(id);
  }, [isRunning, activeChatId, filterDebounced, refreshChats]);

  // Status poller — always on while the page is mounted so we detect
  // externally-stopped sessions (e.g. server restart).
  useEffect(() => {
    refreshStatus();
    const id = setInterval(refreshStatus, STATUS_POLL_MS);
    return () => clearInterval(id);
  }, [refreshStatus]);

  useEffect(() => {
    const serverChatId = isRunning ? status?.active_chat_id || null : null;
    if (activeChatRef.current === serverChatId) return;

    messagesRequestRef.current += 1;
    activeChatRef.current = serverChatId;
    setActiveChatId(serverChatId);
    setMessages([]);
    if (!serverChatId) setMsgsLoading(false);
  }, [isRunning, status?.active_chat_id]);

  // Messages poller — only while a chat is active.
  useEffect(() => {
    if (!isRunning || !activeChatId) {
      setMessages([]);
      return undefined;
    }
    refreshMessages();
    const id = setInterval(refreshMessages, MESSAGES_POLL_MS);
    return () => clearInterval(id);
  }, [isRunning, activeChatId, refreshMessages]);

  // Auto-scroll the message pane to the bottom when new messages arrive.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  // Countdown ticker — drives the "Sending in Ns..." label while the server's
  // anti-block throttle is in effect. Decrement every 250ms.
  useEffect(() => {
    if (sendCountdown <= 0) return undefined;
    const id = setInterval(() => {
      setSendCountdown((s) => (s > 0 ? Math.max(0, s - 0.25) : 0));
    }, 250);
    return () => clearInterval(id);
  }, [sending, sendCountdown]);

  // ── Actions ───────────────────────────────────────────────────────────────

  const handleStart = async () => {
    if (isStarting) return;
    setIsStarting(true);
    freezeStatusRef.current = true;
    statusRequestRef.current += 1;
    try {
      const { data } = await whatsappLiveApi.start();
      // Apply the action response directly. Previously refreshStatus was called
      // while frozen, so it returned early and the UI looked stopped for up to
      // the next five-second poll even though Chromium was already running.
      const serverChatId = data.active_chat_id || null;
      activeChatRef.current = serverChatId;
      setStatus(data);
      setActiveChatId(serverChatId);
      toast.success('Live chat started — the scanner paused for the session.');
      await refreshChats('', { silent: false });
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to start live chat.'));
    } finally {
      freezeStatusRef.current = false;
      setIsStarting(false);
    }
  };

  const handleStop = async () => {
    if (isStopping) return;
    setIsStopping(true);
    freezeStatusRef.current = true;
    statusRequestRef.current += 1;
    messagesRequestRef.current += 1;
    try {
      const { data } = await whatsappLiveApi.stop();
      activeChatRef.current = null;
      setActiveChatId(null);
      setStatus(data);
      toast.success('Live chat closed — the scanner resumed.');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to stop live chat.'));
    } finally {
      freezeStatusRef.current = false;
      setIsStopping(false);
    }
  };

  const handlePickChat = async (chatId) => {
    if (openingChatId) return;
    setOpeningChatId(chatId);
    freezeStatusRef.current = true;
    statusRequestRef.current += 1;
    messagesRequestRef.current += 1;
    try {
      const { data } = await whatsappLiveApi.openChat(chatId);
      if (!data.ok) {
        toast.error(data.error || 'Could not open that chat.');
        return;
      }
      activeChatRef.current = chatId;
      setActiveChatId(chatId);
      setStatus((prev) => prev ? {
        ...prev,
        active_chat_id: chatId,
        active_chat_name: data.name || prev.active_chat_name,
      } : prev);
      setMessages([]);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to open chat.'));
    } finally {
      freezeStatusRef.current = false;
      setOpeningChatId(null);
    }
  };

  const handleBackToList = async () => {
    freezeStatusRef.current = true;
    statusRequestRef.current += 1;
    messagesRequestRef.current += 1;
    try {
      await whatsappLiveApi.closeChat();
      activeChatRef.current = null;
      setActiveChatId(null);
      setStatus((prev) => prev ? {
        ...prev,
        active_chat_id: null,
        active_chat_name: null,
      } : prev);
      setMessages([]);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to close chat.'));
    } finally {
      freezeStatusRef.current = false;
    }
  };

  const handleSend = async (event) => {
    event?.preventDefault?.();
    const text = draft.trim();
    if (!text || !isRunning || !activeChatId) return;
    if (sending) return;

    // Client-side throttle: surface the wait so the user sees the
    // anti-block pace before the server-side delay kicks in.
    const elapsed = (Date.now() - lastSendTsRef.current) / 1000;
    const remaining = Math.max(0, SEND_THROTTLE_SECONDS - elapsed);
    if (remaining > 0) {
      setSendCountdown(Math.ceil(remaining));
      // Don't return — wait, then send. The server mirrors the wait, so
      // the result never comes back before the client has finished its
      // visual countdown.
      await new Promise((resolve) => setTimeout(resolve, remaining * 1000));
    }

    setSending(true);
    try {
      const { data } = await whatsappLiveApi.sendMessage(text);
      setDraft('');
      lastSendTsRef.current = Date.now();
      // Force a refresh so the message appears immediately (the next 3s poll
      // would also pick it up, but a real-time UX feels snappier).
      await refreshMessages();
      // toast.success is intentionally omitted — the message bubble itself is
      // the success signal, and chaining toasts with rapid sends is noisy.
      if (data?.throttled_seconds > 0.5) {
        toast(`Sent (server waited ~${data.throttled_seconds.toFixed(1)}s)`, {
          icon: '⏳',
          duration: 2500,
        });
      }
    } catch (err) {
      toast.error(getErrorMessage(err, 'Send failed. The browser may have lost focus.'));
    } finally {
      setSending(false);
      setSendCountdown(0);
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────

  const busy = isStarting || isStopping;
  const sendDisabled = !isRunning || !activeChatId || sending || !draft.trim();

  return (
    <div className="mx-auto flex h-[calc(100vh-7rem)] w-full max-w-7xl flex-col gap-4 px-4 py-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">WhatsApp Live Chat</h1>
          <p className="mt-0.5 text-sm text-zinc-400">
            Click a chat, read messages, and reply in the box below. While live, the
            scheduled scanner is paused.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={statusInfo} />
          {!isRunning ? (
            <button
              type="button"
              onClick={handleStart}
              disabled={busy}
              className="btn-primary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid="live-start-button"
            >
              {isStarting ? <Spinner className="h-4 w-4" /> : null}
              {isStarting ? 'Starting…' : 'Start live chat'}
            </button>
          ) : (
            <button
              type="button"
              onClick={handleStop}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg border border-red-700/40 bg-red-500/10 px-4 py-2 text-sm font-medium text-red-200 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid="live-stop-button"
            >
              {isStopping ? <Spinner className="h-4 w-4" /> : null}
              {isStopping ? 'Closing…' : 'Stop live chat'}
            </button>
          )}
        </div>
      </div>

      {/* Two-pane body */}
      <div className="card flex min-h-0 flex-1 overflow-hidden p-0">
        {/* Sidebar */}
        <aside className="flex w-80 shrink-0 flex-col border-r border-surface-700 bg-surface-850">
          <div className="border-b border-surface-700 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="text-xs font-medium text-zinc-300">10 most recent chats</p>
              <span className="text-[10px] uppercase tracking-wide text-zinc-600">Live</span>
            </div>
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search all chats…"
              aria-label="Search WhatsApp chats"
              disabled={!isRunning}
              className="w-full rounded-md border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-500 focus:border-accent-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
              data-testid="live-chat-filter"
            />
          </div>
          <div className="flex-1 overflow-y-auto" data-testid="live-chat-list">
            {!isRunning ? (
              <EmptyChatsState
                title="Start live chat to see your conversations"
                subtitle="Click the button above to open a dedicated WhatsApp browser."
              />
            ) : listLoading && chats.length === 0 ? (
              <div className="flex items-center justify-center gap-2 py-8 text-sm text-zinc-500">
                <Spinner className="h-4 w-4" />
                Loading chats…
              </div>
            ) : chats.length === 0 ? (
              <EmptyChatsState
                title="No chats match"
                subtitle={
                  filter
                    ? 'Try a shorter filter or clear the search.'
                    : 'Make sure WhatsApp is connected in this account.'
                }
              />
            ) : (
              <ul>
                {chats.map((chat) => (
                  <li key={chat.chat_id}>
                    <button
                      type="button"
                      onClick={() => handlePickChat(chat.chat_id)}
                      disabled={Boolean(openingChatId)}
                      data-testid={`live-chat-row-${chat.chat_id}`}
                      className={`flex w-full items-start gap-3 border-b border-surface-800 px-3 py-3 text-left transition hover:bg-surface-800 disabled:cursor-wait disabled:opacity-60 ${
                        activeChatId === chat.chat_id ? 'bg-surface-800' : ''
                      }`}
                    >
                      <Avatar name={chat.name} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-sm font-medium text-zinc-100">
                            {chat.name}
                          </p>
                          {chat.unread_count > 0 && (
                            <span className="shrink-0 rounded-full bg-accent-500 px-2 py-0.5 text-[10px] font-semibold text-surface-950">
                              {chat.unread_count}
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 truncate text-xs text-zinc-500">
                          {chat.preview || <span className="italic text-zinc-600">no preview</span>}
                        </p>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>

        {/* Active chat pane */}
        <section className="flex min-w-0 flex-1 flex-col bg-surface-900">
          {!isRunning ? (
            <ActiveChatIdle
              title="Live chat is not running"
              subtitle="Start it from the toolbar to chat with any selected conversation."
            />
          ) : !activeChatId ? (
            <ActiveChatIdle
              title="Pick a chat to start"
              subtitle="Click a chat on the left to read and send messages."
            />
          ) : (
            <>
              <header className="flex items-center justify-between border-b border-surface-700 px-4 py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-zinc-100">
                    {status?.active_chat_name || 'Chat'}
                  </p>
                  <p className="truncate text-xs text-zinc-500">
                    {msgsLoading ? 'Refreshing…' : 'Polling every 3s'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={handleBackToList}
                  className="rounded-md p-2 text-zinc-400 transition hover:bg-surface-700 hover:text-zinc-100"
                  aria-label="Back to chat list"
                  data-testid="live-chat-back"
                >
                  <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M12.7 4.3a.75.75 0 0 0-1.06 0L5.7 10.23a.75.75 0 0 0 0 1.06l5.94 5.94a.75.75 0 1 0 1.06-1.06L7.28 10.7l5.42-5.43a.75.75 0 0 0 0-1.06Z" />
                  </svg>
                </button>
              </header>

              <div
                className="flex-1 space-y-3 overflow-y-auto px-4 py-4"
                data-testid="live-message-list"
              >
                {messages.length === 0 && !msgsLoading ? (
                  <p className="py-8 text-center text-sm text-zinc-500">
                    No messages visible yet — they'll appear here as WhatsApp loads them.
                  </p>
                ) : (
                  messages.map((msg, idx) => (
                    <MessageBubble key={msg.whatsapp_message_id || idx} msg={msg} />
                  ))
                )}
                <div ref={messagesEndRef} />
              </div>

              <form
                onSubmit={handleSend}
                className="flex items-center gap-2 border-t border-surface-700 px-3 py-3"
                data-testid="live-chat-form"
              >
                <input
                  type="text"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder={
                    sendCountdown > 0
                      ? `Anti-block pacing — sending in ${sendCountdown.toFixed(1)}s…`
                      : 'Type a message and press Enter to send'
                  }
                  disabled={sending}
                  autoFocus
                  className="flex-1 rounded-md border border-surface-700 bg-surface-950 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-500 focus:border-accent-500 focus:outline-none disabled:opacity-50"
                  data-testid="live-chat-input"
                />
                <button
                  type="submit"
                  disabled={sendDisabled}
                  className="btn-primary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
                  data-testid="live-chat-send"
                >
                  {sending ? <Spinner className="h-4 w-4" /> : null}
                  {sending ? 'Sending…' : 'Send'}
                </button>
              </form>
              {sendCountdown > 0 && (
                <p
                  className="border-t border-surface-700 bg-surface-800 px-4 py-2 text-xs text-zinc-400"
                  data-testid="live-chat-throttle-note"
                >
                  ⏳ WhatsApp blocks rapid sends. We're holding this message for{' '}
                  <span className="font-mono text-zinc-200">
                    {sendCountdown.toFixed(1)}s
                  </span>{' '}
                  before it goes out (anti-block pacing, default 10s).
                </p>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────────────

function StatusBadge({ status }) {
  const tones = {
    active: 'bg-green-500/10 text-green-300 ring-green-500/25',
    starting: 'bg-yellow-500/10 text-yellow-300 ring-yellow-500/25',
    error: 'bg-red-500/10 text-red-300 ring-red-500/25',
    idle: 'bg-surface-700 text-zinc-300 ring-surface-600',
  };
  const tone = tones[status.tone] || tones.idle;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${tone}`}
      data-testid="live-status-badge"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {status.label}
    </span>
  );
}

function Avatar({ name }) {
  // Stable color derived from the first character of the chat name so the same
  // chat produces the same hue across renders (helps recognise groups).
  const initial = (name || '?').trim().charAt(0).toUpperCase() || '?';
  const hue = ((initial.charCodeAt(0) || 0) * 47) % 360;
  return (
    <div
      className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-sm font-semibold text-surface-950"
      style={{ backgroundColor: `hsl(${hue}, 70%, 55%)` }}
      aria-hidden="true"
    >
      {initial}
    </div>
  );
}

function MessageBubble({ msg }) {
  const outgoing = msg.is_outgoing;
  return (
    <div className={`flex ${outgoing ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[78%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm shadow-sm ${
          outgoing
            ? 'rounded-br-sm bg-accent-500 text-surface-950'
            : 'rounded-bl-sm bg-surface-800 text-zinc-100'
        }`}
        data-testid={`live-message-${outgoing ? 'out' : 'in'}`}
      >
        {!outgoing && msg.sender ? (
          <p className="mb-0.5 text-xs font-medium opacity-70">{msg.sender}</p>
        ) : null}
        <p>{msg.text || (msg.type === 'image' ? '📷 Image' : '')}</p>
      </div>
    </div>
  );
}

function EmptyChatsState({ title, subtitle }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <p className="text-sm font-medium text-zinc-300">{title}</p>
      <p className="mt-1 text-xs text-zinc-500">{subtitle}</p>
    </div>
  );
}

function ActiveChatIdle({ title, subtitle }) {
  return (
    <div className="flex flex-1 items-center justify-center p-8 text-center">
      <div>
        <p className="text-sm font-medium text-zinc-300">{title}</p>
        <p className="mt-1 text-xs text-zinc-500">{subtitle}</p>
      </div>
    </div>
  );
}
