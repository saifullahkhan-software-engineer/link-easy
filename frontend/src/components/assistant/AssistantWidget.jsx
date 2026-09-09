import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { assistantApi } from '../../api/assistant';
import useSpeechRecognition, { RECOGNITION_LANGS } from '../../hooks/useSpeechRecognition';
import { containsUrduScript, detectSpeechLang, pickVoice, toSpeakableText } from '../../utils/speakable';
import { COMPOSE_KEY as LS_COMPOSE } from '../../utils/assistantCompose';

/**
 * The floating AI Assistant — available on every /app page.
 *
 * One chat surface for: checking messages across the connected channels,
 * "where do I…?" questions answered from the backend's app guide, being
 * walked to the right screen, and — with confirmation — sending messages
 * (the assistant opens the chat, fills the draft, asks, then sends).
 *
 * Voice: the mic button uses the browser's Web Speech API (Chrome/Edge/Safari);
 * on unsupported browsers it simply stays hidden. The mic stays open once
 * tapped: a 1–2s pause ends one command (it is sent automatically) and
 * listening continues for the next command. Read-aloud uses speechSynthesis
 * with sanitised, markdown-free text and an Urdu voice when the reply is in
 * Urdu. Both run locally in the browser.
 */

const LS_CONVERSATION = 'le.assistant.conversation_id';
const LS_OPEN = 'le.assistant.open';
const LS_AUTO_NAV = 'le.assistant.auto_navigate';
const LS_READ_ALOUD = 'le.assistant.read_aloud';
const LS_MIC_LANG = 'le.assistant.mic_lang';
// Cross-page handoff key (COMPOSE_KEY, imported above): an `open_chat` action
// stores the draft, the inbox page consumes it on mount.

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

/* ── tiny markdown renderer (no deps) ────────────────────────────────────────
 * Assistant replies use **bold**, `code` and "- " bullets. Rendering them as
 * text keeps the panel readable; read-aloud separately strips them via
 * toSpeakableText. Built with React nodes only — never innerHTML.
 */

function renderInline(text, keyPrefix) {
  const parts = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0;
  let match;
  let i = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith('**')) {
      parts.push(
        <strong key={`${keyPrefix}-${i}`} className="font-semibold text-zinc-100">
          {token.slice(2, -2)}
        </strong>
      );
    } else if (token.startsWith('`')) {
      parts.push(
        <code
          key={`${keyPrefix}-${i}`}
          className="rounded bg-surface-900 px-1 py-0.5 font-mono text-[12px] text-accent-200"
        >
          {token.slice(1, -1)}
        </code>
      );
    } else {
      parts.push(<em key={`${keyPrefix}-${i}`}>{token.slice(1, -1)}</em>);
    }
    i += 1;
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function RichText({ text }) {
  const lines = String(text || '').split('\n');
  const blocks = [];
  let bullets = [];
  const flushBullets = () => {
    if (!bullets.length) return;
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="list-disc space-y-1 pl-5">
        {bullets.map((item, i) => (
          <li key={i}>{renderInline(item, `li-${blocks.length}-${i}`)}</li>
        ))}
      </ul>
    );
    bullets = [];
  };
  lines.forEach((line, index) => {
    const bullet = line.match(/^\s*[-*+•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const heading = line.match(/^\s{0,3}#{1,6}\s+(.*)$/);
    if (bullet || numbered) {
      bullets.push(bullet ? bullet[1] : numbered[1]);
    } else {
      flushBullets();
      if (line.trim() === '') {
        blocks.push(<div key={`sp-${index}`} className="h-1.5" />);
      } else if (heading) {
        blocks.push(
          <p key={`h-${index}`} className="font-semibold text-zinc-100">
            {renderInline(heading[1], `h-${index}`)}
          </p>
        );
      } else {
        blocks.push(<p key={`p-${index}`}>{renderInline(line, `p-${index}`)}</p>);
      }
    }
  });
  flushBullets();
  return <div className="space-y-1">{blocks}</div>;
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

function SpeakerIcon({ className = 'h-3.5 w-3.5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z"
      />
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
function MessageBubble({ message, onNavigate, onOpenChat, onReplay, replaying }) {
  const isUser = message.role === 'user';
  const isUrdu = !isUser && containsUrduScript(message.content);
  return (
    <div className={`flex flex-col gap-2 ${isUser ? 'items-end' : 'items-start'}`}>
      <div
        className={`max-w-[92%] break-words rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
          isUser
            ? 'rounded-br-md bg-accent-500/15 text-accent-50 ring-1 ring-inset ring-accent-500/25'
            : 'rounded-bl-md bg-surface-800 text-zinc-200 ring-1 ring-inset ring-surface-700'
        }`}
        dir={isUrdu ? 'rtl' : 'auto'}
      >
        {isUser ? <span className="whitespace-pre-wrap">{message.content}</span> : <RichText text={message.content} />}
      </div>

      {!isUser && (
        <button
          type="button"
          onClick={() => onReplay(message.content)}
          title={replaying ? 'Stop reading' : 'Read this message aloud'}
          className="flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] text-zinc-500 transition hover:bg-surface-800 hover:text-zinc-200"
        >
          <SpeakerIcon />
          {replaying ? 'Stop' : 'Listen'}
        </button>
      )}

      {!isUser && message.channels?.length > 0 && (
        <div className="w-full space-y-1.5">
          {message.channels.map((channel) => (
            <ChannelCard key={channel.channel} channel={channel} onOpen={onNavigate} />
          ))}
        </div>
      )}

      {!isUser && message.actions?.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {message.actions.map((action, index) => {
            const isChat = action.type === 'open_chat';
            return (
              <button
                key={index}
                type="button"
                onClick={() => (isChat ? onOpenChat(action) : onNavigate(action.path))}
                className="flex items-center gap-1.5 rounded-full border border-accent-500/30 bg-accent-500/10 px-3 py-1.5 text-xs font-medium text-accent-200 transition hover:bg-accent-500/20"
              >
                {isChat ? `💬 ${action.label || 'Open chat'}` : action.label || action.path}
                <span aria-hidden="true">→</span>
              </button>
            );
          })}
        </div>
      )}

      {!isUser && message.actions?.some((a) => a.type === 'open_chat' && a.draft) && (
        <p className="text-[11px] text-zinc-500">
          The draft is typed in the chat box — review it there, then say “yes, send it” to confirm.
        </p>
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
  const [speaking, setSpeaking] = useState(false);

  const { pathname } = useLocation();
  const navigate = useNavigate();
  const scrollRef = useRef(null);
  const micControlsRef = useRef(null);

  // AppLayout remains mounted while React Router changes pages. Persisting
  // this flag also restores the panel after a full refresh, so opening the
  // assistant is not lost when moving between app sections.
  useEffect(() => {
    writeFlag(LS_OPEN, open);
  }, [open]);

  const stopSpeaking = useCallback(() => {
    try {
      window.speechSynthesis?.cancel();
    } catch {}
    setSpeaking(false);
  }, []);

  // Read-aloud: sanitise Markdown/emoji/URLs into plain sentences (otherwise
  // the voice says "asterisk asterisk" and reads emoji names), pick an Urdu
  // voice for Urdu replies, and freeze the mic while talking so it doesn't
  // transcribe its own voice.
  const speak = useCallback(
    (text, { force = false } = {}) => {
      if ((!readAloud && !force) || typeof window === 'undefined' || !window.speechSynthesis) return;
      const clean = toSpeakableText(text);
      if (!clean) return;
      try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(clean);
        const lang = detectSpeechLang(text);
        utterance.lang = lang === 'ur' ? 'ur-PK' : 'en-US';
        const voice = pickVoice(window.speechSynthesis.getVoices(), lang);
        if (voice) utterance.voice = voice;
        utterance.onstart = () => {
          setSpeaking(true);
          micControlsRef.current?.setPaused?.(true);
        };
        const done = () => {
          setSpeaking(false);
          micControlsRef.current?.setPaused?.(false);
        };
        utterance.onend = done;
        utterance.onerror = done;
        window.speechSynthesis.speak(utterance);
      } catch {}
    },
    [readAloud]
  );

  const navigateTo = useCallback(
    (path) => {
      if (!path) return;
      navigate(path);
    },
    [navigate]
  );

  // An `open_chat` action: stash the draft for the inbox page, then go there.
  // The panel stays open (it is a fixed overlay) so the user can confirm.
  const openChatAction = useCallback(
    (action) => {
      if (!action?.path) return;
      try {
        sessionStorage.setItem(
          LS_COMPOSE,
          JSON.stringify({
            channel: action.channel || null,
            conversationId: action.conversation_id || null,
            conversationName: action.conversation_name || '',
            draft: action.draft || '',
            ts: Date.now(),
          })
        );
      } catch {}
      const params = new URLSearchParams({ compose: '1' });
      if (action.conversation_id) params.set('convo', action.conversation_id);
      navigate(`${action.path}?${params.toString()}`);
      toast.success(`Opening ${action.label || 'chat'} — draft is ready for review`, { duration: 2500 });
    },
    [navigate]
  );

  const send = useCallback(
    async (text, { viaVoice = false } = {}) => {
      const message = (text ?? input).trim();
      if (!message || sending) return;
      stopSpeaking();
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
        // my gmail", "message Sara on Instagram") and only while the user
        // hasn't switched it off. open_chat actions also carry the draft.
        const auto = (data.actions || []).filter((a) => a.auto);
        if (auto.length && autoNavigate) {
          const target = auto[auto.length - 1];
          if (target.type === 'open_chat') {
            openChatAction(target);
          } else {
            toast.success(`Opening ${target.label || target.path}`, { duration: 2000 });
            navigate(target.path);
          }
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
    [autoNavigate, conversationId, input, navigate, openChatAction, pathname, sending, speak, stopSpeaking]
  );

  const {
    supported: micSupported,
    listening,
    paused: micPaused,
    transcript,
    error: micError,
    lang: micLang,
    setLang: setMicLang,
    start: micStart,
    stop: micStop,
    setPaused: setMicPaused,
  } = useSpeechRecognition({
    onSpeechStart: () => {
      // Stop explaining / reading aloud immediately as soon as the user starts speaking
      stopSpeaking();
    },
    onFinal: (finalText) => {
      // A finished voice capture (1–2s of quiet) sends immediately, and the
      // mic keeps listening for the next command — hands-free.
      send(finalText, { viaVoice: true });
    },
  });

  micControlsRef.current = { setPaused: setMicPaused };

  // Restore the mic language choice once.
  useEffect(() => {
    try {
      const stored = localStorage.getItem(LS_MIC_LANG);
      if (stored && RECOGNITION_LANGS.some((l) => l.id === stored)) setMicLang(stored);
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const cycleMicLang = () => {
    const order = RECOGNITION_LANGS.map((l) => l.id);
    const next = order[(order.indexOf(micLang) + 1) % order.length];
    setMicLang(next);
    try {
      localStorage.setItem(LS_MIC_LANG, next);
    } catch {}
  };

  const handleMic = () => {
    if (listening) {
      micStop();
    } else {
      stopSpeaking();
      micStart();
    }
  };

  const handleReplay = (text) => {
    if (speaking) stopSpeaking();
    else speak(text, { force: true });
  };

  const startNewChat = () => {
    setMessages([]);
    setConversationId(null);
    try {
      localStorage.removeItem(LS_CONVERSATION);
    } catch {}
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

  // Never leave speech running after the widget unmounts.
  useEffect(() => () => stopSpeaking(), [stopSpeaking]);

  const empty = messages.length === 0;
  const micLangLabel = RECOGNITION_LANGS.find((l) => l.id === micLang)?.label || 'Auto';

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
          {speaking && (
            <button
              type="button"
              onClick={stopSpeaking}
              className="rounded-full bg-red-500/15 px-2.5 py-1 text-[11px] font-medium text-red-200 ring-1 ring-inset ring-red-500/30 transition hover:bg-red-500/25"
              title="Stop reading aloud"
            >
              ⏹ Stop
            </button>
          )}
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
            <MessageBubble
              key={message.id || index}
              message={message}
              onNavigate={navigateTo}
              onOpenChat={openChatAction}
              onReplay={handleReplay}
              replaying={speaking}
            />
          ))}

          {sending && (
            <div className="flex items-center gap-2 rounded-2xl rounded-bl-md bg-surface-800 px-3.5 py-2.5 text-xs text-accent-200 ring-1 ring-inset ring-surface-700 w-fit">
              <span className="relative flex h-2 w-2 shrink-0">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-accent-400" />
              </span>
              <span className="font-medium animate-pulse">Thinking… processing…</span>
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
              <span className="relative flex h-2 w-2 shrink-0">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-accent-400" />
              </span>
              <span className="min-w-0 flex-1 truncate font-medium">
                {sending
                  ? 'Thinking… processing…'
                  : micPaused
                  ? 'Paused while I read the reply…'
                  : transcript || 'Listening… pause 1–2s to send'}
              </span>
              <button
                type="button"
                onClick={cycleMicLang}
                title={`Voice language: ${micLangLabel} — tap to change (Auto / English / Urdu)`}
                className="shrink-0 rounded-full bg-surface-800 px-2 py-0.5 text-[11px] font-medium text-zinc-300 ring-1 ring-inset ring-surface-700 transition hover:text-accent-200"
              >
                {micLangLabel}
              </button>
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
              onFocus={() => stopSpeaking()}
              onChange={(event) => {
                stopSpeaking();
                setInput(event.target.value);
              }}
              onKeyDown={(event) => {
                stopSpeaking();
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
              placeholder={listening ? 'Listening… pause to send, or type' : 'Ask about messages, pages, anything…'}
              dir="auto"
              className="max-h-28 min-h-[42px] flex-1 resize-none rounded-xl border border-surface-700 bg-surface-800 px-3 py-2.5 text-sm text-zinc-100 placeholder-zinc-500 outline-none transition focus:border-accent-500/50"
            />
            {micSupported && (
              <button
                type="button"
                onClick={handleMic}
                title={listening ? 'Stop listening (mic stays open until you tap)' : 'Speak — mic stays open'}
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
              title="Send"
              className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-xl bg-accent-500 text-surface-950 transition hover:bg-accent-400 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <SendIcon />
            </button>
          </form>
          {listening && (
            <p className="mt-1.5 px-1 text-[11px] text-zinc-500">
              Mic stays open — pause 1–2s to send each command. Tap the mic to close it.
            </p>
          )}
        </div>
      </div>
    ),
    [
      autoNavigate,
      empty,
      handleMic,
      cycleMicLang,
      handleReplay,
      input,
      listening,
      messages,
      micError,
      micLangLabel,
      micPaused,
      micSupported,
      navigateTo,
      openChatAction,
      open,
      readAloud,
      send,
      sending,
      speaking,
      startNewChat,
      stopSpeaking,
      transcript,
    ]
  );

  return (
    <>
      {open && panel}

      {/* The opener only renders while the panel is closed — previously the
          floating close button sat on top of the panel's own send button. */}
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Open AI assistant"
          className="fixed bottom-5 right-5 z-[60] flex h-14 w-14 items-center justify-center rounded-full bg-accent-500 text-surface-950 shadow-lg shadow-accent-500/25 transition hover:bg-accent-400 hover:scale-105"
        >
          <SparkleIcon className="h-6 w-6" />
        </button>
      )}
    </>
  );
}
