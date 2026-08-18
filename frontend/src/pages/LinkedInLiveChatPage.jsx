/**
 * LinkedIn Live Chat page.
 *
 * FILE: frontend/src/pages/LinkedInLiveChatPage.jsx
 *
 * Nearly-identical layout to WhatsAppLiveChatPage: sidebar with
 * chat list + active chat pane with message bubbles + input. The
 * only difference is the LinkedIn visual cues and the API surface.
 *
 * Polling intervals and anti-block pacing mirror the WhatsApp version.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { linkedinLiveApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';

const STATUS_POLL_MS   = 5_000;
const CHATS_POLL_MS    = 8_000;
const MESSAGES_POLL_MS = 3_000;
const FILTER_DEBOUNCE_MS = 300;
const SEND_THROTTLE_SECONDS = 10;

function describeStatus(snap) {
  if (!snap) return { label: 'Not started', tone: 'idle' };
  if (snap.status === 'running') {
    return snap.active_chat_name
      ? { label: `Chatting with ${snap.active_chat_name}`, tone: 'active' }
      : { label: 'Browsing conversations', tone: 'active' };
  }
  if (snap.status === 'starting') return { label: 'Starting…', tone: 'starting' };
  if (snap.status === 'error')    return { label: snap.message || 'Error', tone: 'error' };
  return { label: snap.message || 'Stopped', tone: 'idle' };
}

export default function LinkedInLiveChatPage() {
  const [status, setStatus] = useState(null);
  const [isStarting, setIsStarting] = useState(false);
  const [isStopping, setIsStopping] = useState(false);

  const [chats, setChats] = useState([]);
  const [filter, setFilter] = useState('');
  const [filterDebounced, setFilterDebounced] = useState('');
  const [listLoading, setListLoading] = useState(false);
  const [activeChatId, setActiveChatId] = useState(null);
  const [openingChatId, setOpeningChatId] = useState(null);

  const [messages, setMessages] = useState([]);
  const [msgsLoading, setMsgsLoading] = useState(false);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [sendCountdown, setSendCountdown] = useState(0);

  const messagesEndRef = useRef(null);
  const lastSendTsRef = useRef(0);
  const freezeStatusRef = useRef(false);
  const filterTimerRef = useRef(null);
  const activeChatRef = useRef(null);
  const messagesRequestRef = useRef(0);
  const statusRequestRef = useRef(0);

  const isRunning = status?.status === 'running';
  const statusInfo = useMemo(() => describeStatus(status), [status]);

  const refreshStatus = useCallback(async () => {
    if (freezeStatusRef.current) return;
    const requestId = ++statusRequestRef.current;
    try {
      const { data } = await linkedinLiveApi.getStatus();
      if (requestId === statusRequestRef.current && !freezeStatusRef.current) {
        setStatus(data);
      }
    } catch { /* keep last known status */ }
  }, []);

  const refreshChats = useCallback(async (rawFilter, { silent = true } = {}) => {
    try {
      if (!silent) setListLoading(true);
      const { data } = await linkedinLiveApi.listChats({
        q: rawFilter || '',
        limit: 50,
      });
      setChats(data.chats || []);
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
      const { data } = await linkedinLiveApi.getMessages({ limit: 50 });
      // A response from the previously selected chat must never overwrite the
      // newly opened conversation.
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
        const message = detail || 'The server did not return an error description.';
        toast.error(
          message.startsWith('Could not read LinkedIn messages:')
            ? message
            : `Could not read LinkedIn messages: ${message}`,
          { id: 'linkedin-live-msgs' },
        );
      }
    } finally {
      if (requestId === messagesRequestRef.current) setMsgsLoading(false);
    }
  }, [activeChatId]);

  useEffect(() => {
    if (filterTimerRef.current) clearTimeout(filterTimerRef.current);
    filterTimerRef.current = setTimeout(
      () => setFilterDebounced(filter),
      FILTER_DEBOUNCE_MS,
    );
    return () => filterTimerRef.current && clearTimeout(filterTimerRef.current);
  }, [filter]);

  useEffect(() => {
    if (!isRunning) {
      setChats([]);
      return undefined;
    }
    // Keep list polling from competing with message snapshots on the shared
    // LinkedIn page while a conversation is selected.
    if (activeChatId) return undefined;
    refreshChats(filterDebounced, { silent: false });
    const id = setInterval(() => refreshChats(filterDebounced), CHATS_POLL_MS);
    return () => clearInterval(id);
  }, [isRunning, activeChatId, filterDebounced, refreshChats]);

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

  useEffect(() => {
    if (!isRunning || !activeChatId) return undefined;
    refreshMessages();
    const id = setInterval(refreshMessages, MESSAGES_POLL_MS);
    return () => clearInterval(id);
  }, [isRunning, activeChatId, refreshMessages]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  useEffect(() => {
    if (sendCountdown <= 0) return undefined;
    const id = setInterval(() => {
      setSendCountdown((s) => (s > 0 ? Math.max(0, s - 0.25) : 0));
    }, 250);
    return () => clearInterval(id);
  }, [sending, sendCountdown]);

  const handleStart = async () => {
    if (isStarting) return;
    setIsStarting(true);
    freezeStatusRef.current = true;
    statusRequestRef.current += 1;
    try {
      const { data } = await linkedinLiveApi.start();
      setStatus(data);
      const serverChatId = data.active_chat_id || null;
      activeChatRef.current = serverChatId;
      setActiveChatId(serverChatId);
      toast.success('LinkedIn live chat started.');
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
      const { data } = await linkedinLiveApi.stop();
      activeChatRef.current = null;
      setActiveChatId(null);
      setStatus(data);
      toast.success('LinkedIn live chat closed.');
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
      const { data } = await linkedinLiveApi.openChat(chatId);
      if (!data.ok) {
        toast.error(data.error || 'Could not open that conversation.');
        return;
      }
      activeChatRef.current = chatId;
      setActiveChatId(chatId);
      setStatus((previous) => previous ? {
        ...previous,
        active_chat_id: chatId,
        active_chat_name: data.name || previous.active_chat_name,
      } : previous);
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
      await linkedinLiveApi.closeChat();
      activeChatRef.current = null;
      setActiveChatId(null);
      setStatus((previous) => previous ? {
        ...previous,
        active_chat_id: null,
        active_chat_name: null,
      } : previous);
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
    if (!text || !isRunning || !activeChatId || sending) return;

    const elapsed = (Date.now() - lastSendTsRef.current) / 1000;
    const remaining = Math.max(0, SEND_THROTTLE_SECONDS - elapsed);
    if (remaining > 0) {
      setSendCountdown(Math.ceil(remaining));
      await new Promise((resolve) => setTimeout(resolve, remaining * 1000));
    }

    setSending(true);
    try {
      await linkedinLiveApi.sendMessage(text);
      setDraft('');
      lastSendTsRef.current = Date.now();
      await refreshMessages();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Send failed. The browser may have lost focus.'));
    } finally {
      setSending(false);
      setSendCountdown(0);
    }
  };

  const busy = isStarting || isStopping;
  const sendDisabled = !isRunning || !activeChatId || sending || !draft.trim();

  return (
    <div className="mx-auto flex h-[calc(100vh-7rem)] w-full max-w-7xl flex-col gap-4 px-4 py-6 lg:px-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">LinkedIn Live Chat</h1>
          <p className="mt-0.5 text-sm text-zinc-400">
            Pick any conversation and reply manually. While live, the scan
            task is paused so we don't fight the same browser.
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
              data-testid="linkedin-live-start"
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
              data-testid="linkedin-live-stop"
            >
              {isStopping ? <Spinner className="h-4 w-4" /> : null}
              {isStopping ? 'Closing…' : 'Stop live chat'}
            </button>
          )}
        </div>
      </div>

      <div className="card flex min-h-0 flex-1 overflow-hidden p-0">
        <aside className="flex w-80 shrink-0 flex-col border-r border-surface-700 bg-surface-850">
          <div className="border-b border-surface-700 p-3">
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter conversations…"
              disabled={!isRunning}
              className="w-full rounded-md border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-500 focus:border-accent-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
              data-testid="linkedin-chat-filter"
            />
          </div>
          <div className="flex-1 overflow-y-auto" data-testid="linkedin-chat-list">
            {!isRunning ? (
              <LinkedInEmptyChats
                title="Start live chat to see conversations"
                subtitle="LinkedIn will load your message threads on the left."
              />
            ) : listLoading && chats.length === 0 ? (
              <div className="flex items-center justify-center gap-2 py-8 text-sm text-zinc-500">
                <Spinner className="h-4 w-4" /> Loading…
              </div>
            ) : chats.length === 0 ? (
              <LinkedInEmptyChats
                title="No conversations match"
                subtitle={filter ? 'Try a shorter filter or clear the search.' : 'Open LinkedIn messaging once to populate the list.'}
              />
            ) : (
              <ul>
                {chats.map((chat) => (
                  <li key={chat.chat_id}>
                    <button
                      type="button"
                      onClick={() => handlePickChat(chat.chat_id)}
                      disabled={Boolean(openingChatId)}
                      data-testid={`linkedin-chat-row-${chat.chat_id}`}
                      className={`flex w-full items-start gap-3 border-b border-surface-800 px-3 py-3 text-left transition hover:bg-surface-800 disabled:cursor-wait disabled:opacity-60 ${
                        activeChatId === chat.chat_id ? 'bg-surface-800' : ''
                      }`}
                    >
                      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-accent-500/15 text-sm font-bold text-accent-300">
                        {(chat.name || '?').slice(0, 1).toUpperCase()}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-sm font-medium text-zinc-100">{chat.name}</p>
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

        <section className="flex min-w-0 flex-1 flex-col bg-surface-900">
          {!isRunning ? (
            <LinkedInIdle
              title="Live chat is not running"
              subtitle="Start it from the toolbar to chat with any conversation."
            />
          ) : !activeChatId ? (
            <LinkedInIdle
              title="Pick a conversation"
              subtitle="Click a chat on the left to read and send messages."
            />
          ) : (
            <>
              <header className="flex items-center justify-between border-b border-surface-700 px-4 py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-zinc-100">
                    {status?.active_chat_name || 'Conversation'}
                  </p>
                  <p className="truncate text-xs text-zinc-500">
                    {msgsLoading ? 'Refreshing…' : 'Polling every 3s'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={handleBackToList}
                  className="rounded-md p-2 text-zinc-400 transition hover:bg-surface-700 hover:text-zinc-100"
                  aria-label="Back to list"
                  data-testid="linkedin-chat-back"
                >
                  <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M12.7 4.3a.75.75 0 0 0-1.06 0L5.7 10.23a.75.75 0 0 0 0 1.06l5.94 5.94a.75.75 0 1 0 1.06-1.06L7.28 10.7l5.42-5.43a.75.75 0 0 0 0-1.06Z" />
                  </svg>
                </button>
              </header>

              <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4" data-testid="linkedin-message-list">
                {messages.length === 0 && !msgsLoading ? (
                  <p className="py-8 text-center text-sm text-zinc-500">
                    No messages visible yet — they'll appear here as LinkedIn loads them.
                  </p>
                ) : (
                  messages.map((msg, idx) => (
                    <LinkedInMessageBubble key={msg.message_id || `${msg.sender}-${idx}-${msg.text.slice(0, 8)}`} msg={msg} />
                  ))
                )}
                <div ref={messagesEndRef} />
              </div>

              <form
                onSubmit={handleSend}
                className="flex items-center gap-2 border-t border-surface-700 px-3 py-3"
                data-testid="linkedin-chat-form"
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
                  data-testid="linkedin-chat-input"
                />
                <button
                  type="submit"
                  disabled={sendDisabled}
                  className="btn-primary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
                  data-testid="linkedin-chat-send"
                >
                  {sending ? <Spinner className="h-4 w-4" /> : null}
                  {sending ? 'Sending…' : 'Send'}
                </button>
              </form>

              {sendCountdown > 0 && (
                <p
                  className="border-t border-surface-700 bg-surface-800 px-4 py-2 text-xs text-zinc-400"
                  data-testid="linkedin-chat-throttle-note"
                >
                  ⏳ LinkedIn flags very-fast sends as suspicious. Holding this
                  message for{' '}
                  <span className="font-mono text-zinc-200">{sendCountdown.toFixed(1)}s</span>{' '}
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
      data-testid="linkedin-status-badge"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {status.label}
    </span>
  );
}

function LinkedInMessageBubble({ msg }) {
  const outgoing = msg.is_outgoing;
  return (
    <div className={`flex ${outgoing ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[78%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm shadow-sm ${
          outgoing
            ? 'rounded-br-sm bg-[#0a66c2] text-white'
            : 'rounded-bl-sm bg-surface-800 text-zinc-100'
        }`}
      >
        {!outgoing && msg.sender ? (
          <p className="mb-0.5 text-xs font-medium opacity-70">{msg.sender}</p>
        ) : null}
        <p>{msg.text}</p>
      </div>
    </div>
  );
}

function LinkedInEmptyChats({ title, subtitle }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <p className="text-sm font-medium text-zinc-300">{title}</p>
      <p className="mt-1 text-xs text-zinc-500">{subtitle}</p>
    </div>
  );
}

function LinkedInIdle({ title, subtitle }) {
  return (
    <div className="flex flex-1 items-center justify-center p-8 text-center">
      <div>
        <p className="text-sm font-medium text-zinc-300">{title}</p>
        <p className="mt-1 text-xs text-zinc-500">{subtitle}</p>
      </div>
    </div>
  );
}
