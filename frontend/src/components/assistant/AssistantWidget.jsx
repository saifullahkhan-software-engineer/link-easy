import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { assistantApi } from '../../api/assistant';
import useSpeechRecognition from '../../hooks/useSpeechRecognition';

/**
 * The floating AI Assistant — available on every /app page.
 *
 * One chat surface for: checking messages across the connected channels,
 * "where do I…?" questions answered from the backend's app guide, and being
 * walked to the right screen. Navigation arrives as actions on the reply:
 * `auto` ones (the user clearly asked "open my gmail") run immediately —
 * unless the user flipped the auto-navigate toggle off — the rest render as
 * buttons.
 *
 * Voice: the mic button uses the browser's Web Speech API (Chrome/Edge/Safari);
 * on unsupported browsers it simply stays hidden. Read-aloud uses
 * speechSynthesis the same way. Both run locally in the browser.
 */

const LS_CONVERSATION = 'le.assistant.conversation_id';
const LS_OPEN = 'le.assistant.open';
const LS_AUTO_NAV = 'le.assistant.auto_navigate';
const LS_READ_ALOUD = 'le.assistant.read_aloud';

const QUICK_PROMPTS = [
  'Check my new messages',
  'What can you do?',
  'How do I schedule a post?',
  'Where do I connect Instagram?',
];

const CHANNEL_BADGES = {
  instagram: 'IG',
  messenger: 'FB',
  whatsapp: 'WA',
  gmail: '@',
};

function readFlag(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value === '1';
  } catch {
    return fallback;
  }
}

function writeFlag(key, value) {
  try {
    localStorage.setItem(key, value ? '1' : '0');
  } catch {}
}

/* ── small pieces ─────────────────────────────────────────────────────────── */

function SparkleIcon({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456Z"
      />
    </svg>
  );
}

function MicIcon({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z"
      />
    </svg>
  );
}

function SendIcon({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.269 20.875L5.999 12Zm0 0h7.5" />
    </svg>
  );
}

/** One channel's summary card — what check_new_messages found. */
function ChannelCard({ channel, onOpen }) {
  const meta = [];
  if (channel.unread_count != null) meta.push(`${channel.unread_count} unread`);
  if (channel.total_conversations != null) meta.push(`${channel.total_conversations} chats`);
  if (channel.status === 'not_running') meta.push('live browser off');
  if (channel.status === 'reconnect_required') meta.push('reconnect needed');
  if (channel.status === 'error') meta.push('temporarily unavailable');
  if (!channel.connected) meta.push('not connected');

  return (
    <button
      type="button"
      onClick={() => onOpen(channel.path)}
      className="w-full rounded-lg border border-surface-700 bg-surface-800/70 px-3 py-2.5 text-left transition hover:border-accent-500/40 hover:bg-surface-800"
    >
      <div className="flex items-center gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-accent-500/15 text-[11px] font-bold text-accent-300">
          {CHANNEL_BADGES[channel.channel] || '•'}
        </span>
        <span className="text-sm font-medium text-zinc-200">{channel.label || channel.channel}</span>
        <span className="ml-auto text-xs text-zinc-500">{meta.join(' · ')}</span>
      </div>
      {channel.conversations?.length > 0 && (
        <ul className="mt-2 space-y-1">
          {channel.conversations.slice(0, 3).map((convo, index) => (
            <li key={index} className="truncate text-xs text-zinc-400">
              <span className="text-zinc-300">{convo.name}</span>
              {convo.preview ? ` — ${convo.preview}` : ''}
            </li>
          ))}
        </ul>
      )}
    </button>
  );
}

/** A message row. Assistant rows carry actions + channel cards. */
function MessageBubble({ message, onNavigate }) {
  const isUser = message.role === 'user';
  return (
    <div className={`flex flex-col gap-2 ${isUser ? 'items-end' : 'items-start'}`}>
      <div
        className={`max-w-[92%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
          isUser
            ? 'rounded-br-md bg-accent-500/15 text-accent-50 ring-1 ring-inset ring-accent-500/25'
            : 'rounded-bl-md bg-surface-800 text-zinc-200 ring-1 ring-inset ring-surface-700'
        }`}
      >
        {message.content}
      </div>

      {!isUser && message.channels?.length > 0 && (
        <div className="w-full space-y-1.5">
          {message.channels.map((channel) => (
            <ChannelCard key={channel.channel} channel={channel} onOpen={onNavigate} />
          ))}
        </div>
      )}

      {!isUser && message.actions?.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {message.actions.map((action, index) => (
            <button
              key={index}
              type="button"
              onClick={() => onNavigate(action.path)}
              className="flex items-center gap-1.5 rounded-full border border-accent-500/30 bg-accent-500/10 px-3 py-1.5 text-xs font-medium text-accent-200 transition hover:bg-accent-500/20"
            >
              {action.label || action.path}
              <span aria-hidden="true">→</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── the widget ───────────────────────────────────────────────────────────── */

export default function AssistantWidget() {
  const [open, setOpen] = useState(() => readFlag(LS_OPEN, false));
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [conversationId, setConversationId] = useState(null);
  const [loaded, setLoaded] = useState(false); // has the initial resume happened
  const [autoNavigate, setAutoNavigate] = useState(() => readFlag(LS_AUTO_NAV, true));
  const [readAloud, setReadAloud] = useState(() => readFlag(LS_READ_ALOUD, false));

  const { pathname } = useLocation();
  const navigate = useNavigate();
  const scrollRef = useRef(null);

  // AppLayout remains mounted while React Router changes pages. Persisting
  // this flag also restores the panel after a full refresh, so opening the
  // assistant is not lost when moving between app sections.
  useEffect(() => {
    writeFlag(LS_OPEN, open);
  }, [open]);

  const speak = useCallback(
    (text) => {
      if (!readAloud || typeof window === 'undefined' || !window.speechSynthesis) return;
      try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = utterance.lang || undefined; // follow the browser locale
        window.speechSynthesis.speak(utterance);
      } catch {}
    },
    [readAloud]
  );

  const send = useCallback(
    async (text, { viaVoice = false } = {}) => {
      const message = (text ?? input).trim();
      if (!message || sending) return;
      setInput('');
      setMessages((prev) => [...prev, { role: 'user', content: message }]);
      setSending(true);
      try {
        const { data } = await assistantApi.chat(message, {
          currentPath: pathname,
          conversationId,
        });
        setConversationId(data.conversation_id);
        try {
          localStorage.setItem(LS_CONVERSATION, data.conversation_id);
        } catch {}
        setMessages((prev) => [
          ...prev,
          {
            id: `a-${Date.now()}`,
            role: 'assistant',
            content: data.reply,
            actions: data.actions || [],
            channels: data.channels || [],
          },
        ]);
        speak(data.reply);

        // Auto-navigation: only for actions the model marked explicit ("open
        // my gmail") and only while the user hasn't switched it off.
        const auto = (data.actions || []).filter((a) => a.auto);
        if (auto.length && autoNavigate) {
          const target = auto[auto.length - 1];
          toast.success(`Opening ${target.label || target.path}`, { duration: 2000 });
          navigate(target.path);
        }
      } catch (error) {
        const status = error?.response?.status;
        const detail = error?.response?.data?.detail;
        if (status === 503) {
          setMessages((prev) => [
            ...prev,
            {
              role: 'assistant',
              content:
                'The AI assistant is not enabled on this instance yet — ask the operator to configure an AI provider key.',
            },
          ]);
        } else if (status === 429) {
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', content: 'You are sending messages faster than I can answer — give it a minute and try again.' },
          ]);
        } else {
          setMessages((prev) => [
            ...prev,
            {
              role: 'assistant',
              content:
                detail && typeof detail === 'string'
                  ? detail
                  : 'Something went wrong reaching the assistant. Try again in a moment.',
            },
          ]);
        }
      } finally {
        setSending(false);
        if (viaVoice) setInput('');
      }
    },
    [autoNavigate, conversationId, input, navigate, pathname, sending, speak]
  );

  const { supported: micSupported, listening, transcript, error: micError, start, stop } =
    useSpeechRecognition({
      onFinal: (finalText) => {
        // A finished voice capture sends immediately — that is what "speak to
        // the assistant" means. The user can always tap to type instead.
        send(finalText, { viaVoice: true });
      },
    });

  const handleMic = () => {
    if (listening) stop();
    else start();
  };

  const startNewChat = () => {
    setMessages([]);
    setConversationId(null);
    try {
      localStorage.removeItem(LS_CONVERSATION);
    } catch {}
  };

  const navigateTo = (path) => {
    if (!path) return;
    navigate(path);
  };

  // Resume the most recent conversation the first time the panel opens.
  useEffect(() => {
    if (!open || loaded) return;
    setLoaded(true);
    (async () => {
      try {
        const stored = localStorage.getItem(LS_CONVERSATION);
        const { data } = await assistantApi.conversations();
        const latest = data?.conversations?.[0];
        const resumeId = stored && data?.conversations?.some((c) => c.id === stored) ? stored : latest?.id;
        if (!resumeId) return;
        const history = await assistantApi.history(resumeId);
        setConversationId(resumeId);
        setMessages(
          (history.data?.messages || []).map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            actions: m.actions || [],
            channels: [],
          }))
        );
      } catch {
        // A fresh chat is a fine fallback; the error surfaces on first send.
      }
    })();
  }, [open, loaded]);

  // Keep the newest message in view.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, sending]);

  const empty = messages.length === 0;

  const panel = useMemo(
    () => (
      <div className="fixed inset-x-0 bottom-0 z-[60] flex h-[82dvh] flex-col overflow-hidden border-t border-surface-700 bg-surface-900 shadow-2xl sm:inset-auto sm:bottom-6 sm:right-6 sm:h-[560px] sm:max-h-[calc(100dvh-96px)] sm:w-[400px] sm:rounded-2xl sm:border animate-slide-up">
        {/* header */}
        <div className="flex items-center gap-2.5 border-b border-surface-700 px-4 py-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-500/15 text-accent-300">
            <SparkleIcon className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-zinc-100">Assistant</p>
            <p className="truncate text-[11px] text-zinc-500">Messages · navigation · how-to</p>
          </div>
          <button
            type="button"
            onClick={startNewChat}
            title="New chat"
            className="rounded-lg p-1.5 text-zinc-400 transition hover:bg-surface-800 hover:text-zinc-100"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487 18.549 2.8a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125" />
            </svg>
          </button>
          <button
            type="button"
            onClick={() => setOpen(false)}
            title="Close"
            className="rounded-lg p-1.5 text-zinc-400 transition hover:bg-surface-800 hover:text-zinc-100"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* toggles */}
        <div className="flex items-center gap-2 border-b border-surface-700 px-4 py-2">
          <button
            type="button"
            onClick={() => {
              setAutoNavigate((v) => {
                writeFlag(LS_AUTO_NAV, !v);
                return !v;
              });
            }}
            className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition ${
              autoNavigate
                ? 'bg-accent-500/15 text-accent-200 ring-1 ring-inset ring-accent-500/30'
                : 'text-zinc-500 ring-1 ring-inset ring-surface-700'
            }`}
            title="When you clearly ask to open a page, the assistant takes you there directly"
          >
            {autoNavigate ? '⚡ Auto-navigate on' : 'Auto-navigate off'}
          </button>
          <button
            type="button"
            onClick={() => {
              setReadAloud((v) => {
                writeFlag(LS_READ_ALOUD, !v);
                return !v;
              });
            }}
            className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition ${
              readAloud
                ? 'bg-accent-500/15 text-accent-200 ring-1 ring-inset ring-accent-500/30'
                : 'text-zinc-500 ring-1 ring-inset ring-surface-700'
            }`}
            title="Read replies aloud with the browser's speech synthesis"
          >
            {readAloud ? '🔊 Read-aloud on' : 'Read-aloud off'}
          </button>
          <span className="ml-auto text-[11px] text-zinc-600">LinkEasy AI</span>
        </div>

        {/* messages */}
        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {empty && (
            <div className="space-y-4 pt-6 text-center">
              <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-500/15 text-accent-300">
                <SparkleIcon className="h-6 w-6" />
              </span>
              <div>
                <p className="text-sm font-medium text-zinc-200">Hi! I can check your messages and take you anywhere in the app.</p>
                <p className="mt-1 text-xs text-zinc-500">Ask me anything, or tap a suggestion.</p>
              </div>
            </div>
          )}

          {messages.map((message, index) => (
            <MessageBubble key={message.id || index} message={message} onNavigate={navigateTo} />
          ))}

          {sending && (
            <div className="flex items-center gap-1.5 rounded-2xl rounded-bl-md bg-surface-800 px-3.5 py-3 ring-1 ring-inset ring-surface-700 w-fit">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent-400 [animation-delay:0ms]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent-400 [animation-delay:120ms]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent-400 [animation-delay:240ms]" />
            </div>
          )}

          {empty && !sending && (
            <div className="flex flex-wrap justify-center gap-1.5 pt-2">
              {QUICK_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => send(prompt)}
                  className="rounded-full border border-surface-700 bg-surface-800/60 px-3 py-1.5 text-xs text-zinc-300 transition hover:border-accent-500/40 hover:text-accent-200"
                >
                  {prompt}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* input */}
        <div className="border-t border-surface-700 p-3">
          {listening && (
            <div className="mb-2 flex items-center gap-2 rounded-lg bg-accent-500/10 px-3 py-2 text-xs text-accent-200 ring-1 ring-inset ring-accent-500/25">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-accent-400" />
              </span>
              {transcript ? transcript : 'Listening…'}
            </div>
          )}
          {micError && !listening && <p className="mb-2 px-1 text-[11px] text-red-300">{micError}</p>}
          <form
            onSubmit={(event) => {
              event.preventDefault();
              send();
            }}
            className="flex items-end gap-2"
          >
            <textarea
              rows={1}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
              placeholder={listening ? 'Listening…' : 'Ask about messages, pages, anything…'}
              className="max-h-28 min-h-[42px] flex-1 resize-none rounded-xl border border-surface-700 bg-surface-800 px-3 py-2.5 text-sm text-zinc-100 placeholder-zinc-500 outline-none transition focus:border-accent-500/50"
            />
            {micSupported && (
              <button
                type="button"
                onClick={handleMic}
                title={listening ? 'Stop listening' : 'Speak'}
                className={`flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-xl border transition ${
                  listening
                    ? 'border-accent-500/50 bg-accent-500/20 text-accent-200'
                    : 'border-surface-700 bg-surface-800 text-zinc-400 hover:text-zinc-100'
                }`}
              >
                <MicIcon />
              </button>
            )}
            <button
              type="submit"
              disabled={!input.trim() || sending}
              className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-xl bg-accent-500 text-surface-950 transition hover:bg-accent-400 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <SendIcon />
            </button>
          </form>
        </div>
      </div>
    ), [
      autoNavigate,
      empty,
      handleMic,
      input,
      listening,
      messages,
      micError,
      micSupported,
      navigateTo,
      open,
      readAloud,
      send,
      sending,
      startNewChat,
      transcript,
    ]
  );

  return (
    <>
      {open && panel}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? 'Close assistant' : 'Open AI assistant'}
        className="fixed bottom-5 right-5 z-[60] flex h-14 w-14 items-center justify-center rounded-full bg-accent-500 text-surface-950 shadow-lg shadow-accent-500/25 transition hover:bg-accent-400 hover:scale-105"
      >
        {open ? (
          <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        ) : (
          <SparkleIcon className="h-6 w-6" />
        )}
      </button>
    </>
  );
}
