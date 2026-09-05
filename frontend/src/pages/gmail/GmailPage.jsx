import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { gmailApi } from '../../api/gmail';
import { getErrorMessage } from '../../api/client';
import Modal from '../../components/Modal';
import { Spinner } from '../../components/Spinner';
import ComposeForm from '../../components/gmail/ComposeForm';
import { Avatar, GmailMark, GmailStatusBadge, LabelChip, formatDateTimeSafe } from '../../components/gmail/GmailBits';
import { formatRelative } from '../../components/social/SocialBits';

const LIVE_CHECK_MS = 45_000;
const SYSTEM_LABELS = ['INBOX', 'STARRED', 'SENT', 'DRAFTS', 'SPAM', 'TRASH'];
const SYSTEM_LABEL_NAMES = {
  INBOX: 'Inbox',
  STARRED: 'Starred',
  SENT: 'Sent',
  DRAFTS: 'Drafts',
  SPAM: 'Spam',
  TRASH: 'Trash',
};

/** Unread badge next to a rail label. */
function CountBadge({ count }) {
  if (!count) return null;
  return (
    <span className="ml-auto rounded-full bg-accent-500/20 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-accent-300">
      {count > 999 ? '999+' : count}
    </span>
  );
}

/**
 * Gmail workspace: connect card → mailbox (labels rail + message list +
 * thread reading pane), with search, per-label unread totals, live "check
 * mail" polling, label/read/star/archive/trash actions, attachment downloads
 * and reply via the compose modal.
 */
export default function GmailPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // Connection
  const [status, setStatus] = useState(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);

  // Mailbox data
  const [labels, setLabels] = useState([]);
  const [messages, setMessages] = useState([]);
  const [pageToken, setPageToken] = useState('');
  const [loadingList, setLoadingList] = useState(false);
  const [listError, setListError] = useState(null);

  // The view being shown: a label id ('INBOX', 'TRASH', a custom label…),
  // null = all mail. `search`/`unreadOnly` further filter it.
  const [labelId, setLabelId] = useState('INBOX');
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [checkedAt, setCheckedAt] = useState(null);

  // Live "check mail" monitor
  const [liveCheck, setLiveCheck] = useState(true);
  const [checking, setChecking] = useState(false);
  const [unreadCount, setUnreadCount] = useState(null);
  const [inboxTotal, setInboxTotal] = useState(null);
  const prevUnreadRef = useRef(null);

  // Reading pane + compose
  const [selectedThreadId, setSelectedThreadId] = useState(null);
  const [thread, setThread] = useState(null);
  const [threadLoading, setThreadLoading] = useState(false);
  const [compose, setCompose] = useState(null);
  const [labelEditorMessageId, setLabelEditorMessageId] = useState(null);

  const viewRef = useRef({ labelId: 'INBOX', search: '', unreadOnly: false, messageIds: [] });

  // ── data loaders ──────────────────────────────────────────────────────────
  const loadStatus = useCallback(async () => {
    try {
      const { data } = await gmailApi.status();
      setStatus(data);
      return data;
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load the Gmail connection'));
      return null;
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  const loadLabels = useCallback(async () => {
    try {
      const { data } = await gmailApi.labels();
      setLabels(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load Gmail labels'));
    }
  }, []);

  const loadList = useCallback(
    async ({ label, q = '', unread = false, token = '', quiet = false } = {}) => {
      setLoadingList(true);
      setListError(null);
      try {
        const params = { max_results: 50 };
        const query = [q.trim() ? q.trim() : null, unread ? 'is:unread' : null]
          .filter(Boolean)
          .join(' ');
        if (query) params.q = query;
        if (label && !q.trim()) params.label_ids = label;
        if (token) params.page_token = token;
        const { data } = await gmailApi.listMessages(params);
        const rows = Array.isArray(data.messages) ? data.messages : [];
        setMessages(token ? (prev) => [...prev, ...rows] : rows);
        setPageToken(data.next_page_token || '');
        viewRef.current = {
          labelId: label ?? null,
          search: q.trim(),
          unreadOnly: unread,
          messageIds: rows.map((m) => m.id),
        };
      } catch (err) {
        const message = getErrorMessage(err, 'Could not load messages');
        setListError(message);
        if (!quiet) toast.error(message);
      } finally {
        setLoadingList(false);
      }
    },
    []
  );

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    if (!status?.connected) return;
    loadLabels();
    loadList({ label: 'INBOX', q: '', unread: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.connected]);

  // Toast the OAuth outcome when Google redirects back here.
  useEffect(() => {
    const connected = searchParams.get('connected');
    const error = searchParams.get('error');
    if (connected === '1') toast.success('Gmail connected — checking your inbox…');
    if (error) toast.error(error);
    if (connected === '1' || error) {
      setSearchParams({}, { replace: true });
      loadStatus();
    }
  }, [searchParams, setSearchParams, loadStatus]);

  // ── live "check mail" tick (monitoring incoming mail) ─────────────────────
  const checkMail = useCallback(
    async (silent = true) => {
      if (!status?.connected) return;
      setChecking(true);
      try {
        const { data } = await gmailApi.unread();
        setUnreadCount(data.unread_in_inbox);
        setInboxTotal(data.inbox_total);
        setCheckedAt(data.checked_at ? new Date(data.checked_at) : new Date());

        const previous = prevUnreadRef.current;
        const current = data.unread_in_inbox ?? 0;
        if (silent && previous !== null && current > previous) {
          const added = current - previous;
          toast.success(`You've got ${added} new unread ${added === 1 ? 'message' : 'messages'}`);
        }
        prevUnreadRef.current = current;

        // On the plain inbox first page, fold newly arrived mail into the
        // list automatically so the screen is never stale.
        const view = viewRef.current;
        const seen = new Set(view.messageIds);
        const fresh = (data.messages || []).some((m) => !seen.has(m.id));
        if (view.labelId === 'INBOX' && !view.search && !view.unreadOnly && fresh) {
          await loadList({ label: 'INBOX', q: '', unread: false, quiet: true });
        }
      } catch (err) {
        if (!silent) toast.error(getErrorMessage(err, 'Could not check for new mail'));
      } finally {
        setChecking(false);
      }
    },
    [status?.connected, loadList]
  );

  useEffect(() => {
    if (!liveCheck || !status?.connected) return undefined;
    const timer = setInterval(() => checkMail(true), LIVE_CHECK_MS);
    return () => clearInterval(timer);
  }, [liveCheck, status?.connected, checkMail]);

  // ── OAuth connect / disconnect ────────────────────────────────────────────
  async function startConnect() {
    setConnecting(true);
    try {
      const { data } = await gmailApi.authUrl();
      // Full-page navigation: Google shows the account chooser / consent.
      window.location.assign(data.auth_url);
    } catch (err) {
      setConnecting(false);
      toast.error(getErrorMessage(err, 'Could not start Google sign-in'));
    }
  }

  async function handleDisconnect() {
    setConfirmDisconnect(false);
    try {
      await gmailApi.disconnect();
      toast.success('Gmail disconnected');
      setStatus((s) => ({ ...(s || {}), connected: false, account_email: '', messages_total: null }));
      setMessages([]);
      setThread(null);
      setSelectedThreadId(null);
      setUnreadCount(null);
      setCheckedAt(null);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not disconnect Gmail'));
    }
  }

  // ── view switching (label / search / unread) ──────────────────────────────
  function openLabel(nextLabelId) {
    setLabelId(nextLabelId);
    setSearch('');
    setSearchInput('');
    setUnreadOnly(false);
    setSelectedThreadId(null);
    setThread(null);
    loadList({ label: nextLabelId, q: '', unread: false });
  }

  function openAllMail() {
    setLabelId(null);
    setSearch('');
    setSearchInput('');
    setUnreadOnly(false);
    setSelectedThreadId(null);
    setThread(null);
    loadList({ label: null, q: '', unread: false });
  }

  function submitSearch(event) {
    if (event) event.preventDefault();
    const q = searchInput.trim();
    setLabelId(null);
    setSearch(q);
    setUnreadOnly(false);
    setSelectedThreadId(null);
    setThread(null);
    loadList({ label: null, q, unread: false });
  }

  function toggleUnreadOnly() {
    const next = !unreadOnly;
    setUnreadOnly(next);
    setSelectedThreadId(null);
    setThread(null);
    loadList({ label: labelId, q: search, unread: next });
  }

  function refreshView() {
    loadList({ label: labelId, q: search, unread: unreadOnly });
  }

  const handleCheckNow = () => {
    setListError(null);
    refreshView();
    checkMail(false);
  };

  // ── reading pane ──────────────────────────────────────────────────────────
  async function openThread(threadId, openedMessageId) {
    setSelectedThreadId(threadId);
    setThreadLoading(true);
    setThread(null);
    try {
      const { data } = await gmailApi.getThread(threadId);
      const msgs = Array.isArray(data.messages) ? data.messages : [];
      setThread({ ...data, messages: msgs });
      // Gmail semantics: opening a message marks *that* message read.
      const opened = msgs.find((m) => m.id === openedMessageId);
      if (opened && !opened.is_read) {
        try {
          const { data: updated } = await gmailApi.modify(openedMessageId, {
            add_label_ids: [],
            remove_label_ids: ['UNREAD'],
          });
          const patchRow = (m) => (m.id === openedMessageId ? { ...m, ...updated } : m);
          setThread((t) => (t ? { ...t, messages: t.messages.map(patchRow) } : t));
          setMessages((rows) => rows.map(patchRow));
        } catch {
          // Cosmetic only — the thread still opens.
        }
      }
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not open the conversation'));
    } finally {
      setThreadLoading(false);
    }
  }

  const applyMessageLabels = async (messageId, addLabelIds, removeLabelIds) => {
    try {
      const { data } = await gmailApi.modify(messageId, {
        add_label_ids: addLabelIds,
        remove_label_ids: removeLabelIds,
      });
      const patchRow = (m) => (m.id === messageId ? { ...m, ...data } : m);
      setThread((t) => (t ? { ...t, messages: t.messages.map(patchRow) } : t));
      setMessages((rows) => rows.map(patchRow));
      setLabelEditorMessageId(null);
      return data;
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not update the message'));
      return null;
    }
  };

  // After archive/trash/mark-read the row may no longer belong to the
  // current view (inbox / unread-only), so drop it from the list.
  const quickAction = async (message, add = [], remove = []) => {
    const updated = await applyMessageLabels(message.id, add, remove);
    if (!updated) return;
    const inInboxOrAll = (labelId === 'INBOX' || (!labelId && !search)) && !unreadOnly;
    const vanished =
      add.includes('TRASH') ||
      (inInboxOrAll && remove.includes('INBOX')) ||
      (unreadOnly && updated.is_read && message.id === updated.id);
    if (vanished) {
      setMessages((rows) => rows.filter((r) => r.id !== message.id));
    }
  };

  const downloadAttachment = async (messageId, attachment) => {
    try {
      const res = await gmailApi.downloadAttachment(messageId, attachment.attachment_id);
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = attachment.filename || 'attachment';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not download the attachment'));
    }
  };

  const buildReplyInitial = (message) => {
    const accountEmail = (status?.account_email || '').toLowerCase();
    const fromEmail = (message.from_email || '').toLowerCase();
    const isOwn = Boolean(fromEmail && accountEmail && fromEmail === accountEmail);
    const formatRecipients = (list) =>
      (list || []).map((a) => (a.name ? `${a.name} <${a.email}>` : a.email)).join(', ');
    const to = isOwn
      ? formatRecipients(message.to)
      : message.from_name
        ? `${message.from_name} <${message.from_email}>`
        : message.from_email;
    const text = message.text_body || message.snippet || '';
    const quoted = text.split('\n').map((line) => `> ${line}`).join('\n');
    const fromLabel = message.from_name ? `${message.from_name} <${message.from_email}>` : message.from_email;
    return {
      to,
      cc: isOwn ? formatRecipients(message.cc) : '',
      subject: /^re:/i.test(message.subject || '') ? message.subject : `Re: ${message.subject || ''}`,
      body: `\n\nOn ${formatDateTimeSafe(message.internal_date || message.date)}, ${fromLabel} wrote:\n\n${quoted}`,
      in_reply_to: message.message_id_header || '',
      references: message.message_id_header || '',
    };
  };

  // ── render ────────────────────────────────────────────────────────────────
  if (loadingStatus) {
    return (
      <div className="flex h-64 items-center justify-center gap-3 text-zinc-400">
        <Spinner className="h-5 w-5" /> Loading Gmail…
      </div>
    );
  }

  const connected = Boolean(status?.connected);
  const labelByName = Object.fromEntries((labels || []).map((l) => [l.id, l]));
  const userLabels = (labels || []).filter((l) => l.type === 'user');
  const railLabels = [...SYSTEM_LABELS.map((id) => labelByName[id]).filter(Boolean), ...userLabels];
  const selectedLabel = labelId ? labelByName[labelId] : null;
  const viewTitle = search
    ? `Results for “${search}”`
    : unreadOnly
      ? `Unread${labelId ? ` in ${SYSTEM_LABEL_NAMES[labelId] || selectedLabel?.name || 'mailbox'}` : ' mail'}`
      : SYSTEM_LABEL_NAMES[labelId] || selectedLabel?.name || 'All mail';

  return (
    <div className="space-y-4">
      {/* ── header ─────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-rose-500/10 text-rose-300">
            <GmailMark className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-zinc-50">Gmail</h1>
            <p className="text-sm text-zinc-400">
              {connected
                ? `${status.account_email} — read, check and send from your own mailbox.`
                : 'Bring your personal Gmail or Google Workspace mailbox in.'}
            </p>
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <GmailStatusBadge status={status} />
          {connected && (
            <>
              <button className="btn-secondary" onClick={() => navigate('/app/gmail/compose')}>
                Compose
              </button>
              <button className="btn-secondary" onClick={() => setConfirmDisconnect(true)}>
                Disconnect
              </button>
            </>
          )}
        </div>
      </div>

      {!connected ? (
        /* ── connect / not-configured card ─────────────────────────────── */
        <div className="card mx-auto mt-6 max-w-xl p-8">
          <div className="flex flex-col items-center text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-rose-500/10 text-rose-300">
              <GmailMark className="h-9 w-9" />
            </div>
            <h2 className="mt-4 text-xl font-semibold text-zinc-100">
              {status?.configured ? 'Connect your Gmail' : 'Gmail needs a setup step first'}
            </h2>
            {status?.configured ? (
              <>
                <p className="mt-2 text-sm leading-relaxed text-zinc-400">
                  LinkEasy connects through Google's official sign-in — it works with free
                  personal <span className="text-zinc-200">@gmail.com</span> accounts and Google
                  Workspace mailboxes. Once connected LinkEasy can:
                </p>
                <ul className="mt-4 w-full space-y-2 text-left text-sm text-zinc-300">
                  <li className="flex items-start gap-2">
                    <span className="mt-0.5 text-emerald-400">✓</span> Check for and read new
                    messages, and read whole conversations
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="mt-0.5 text-emerald-400">✓</span> Search the mailbox and
                    manage labels, read/unread, archive and trash
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="mt-0.5 text-emerald-400">✓</span> Compose and send replies
                  </li>
                </ul>
                <button className="btn-primary mt-6" onClick={startConnect} disabled={connecting}>
                  {connecting ? <Spinner className="h-4 w-4" /> : <GmailMark className="h-4 w-4" />}
                  {connecting ? 'Opening Google…' : 'Connect Gmail'}
                </button>
                <p className="mt-4 max-w-md text-xs leading-relaxed text-zinc-500">
                  Permission is limited to what the feature needs (read/modify + send). LinkEasy
                  never asks for full mailbox access, and Google rate-limits bulk sending — this
                  is for your own outreach and replies, not cold-email blasts.
                </p>
              </>
            ) : (
              <p className="mt-2 text-sm leading-relaxed text-zinc-400">
                This instance has no Google OAuth client configured yet, so no one can connect
                Gmail. Ask the operator to set{' '}
                <code className="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-zinc-200">
                  GOOGLE_CLIENT_ID
                </code>{' '}
                and{' '}
                <code className="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-zinc-200">
                  GOOGLE_CLIENT_SECRET
                </code>{' '}
                (see <span className="text-zinc-300">docs/gmail_setup.md</span>).
              </p>
            )}
          </div>
        </div>
      ) : (
        <>
          {/* ── toolbar ────────────────────────────────────────────────── */}
          <div className="card flex flex-wrap items-center gap-3 p-4">
            <form onSubmit={submitSearch} className="flex min-w-[16rem] flex-1 items-center gap-2">
              <input
                className="input-field"
                placeholder="Search mail — e.g. from:alice, subject:invoice, is:unread"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
              />
              <button type="submit" className="btn-secondary shrink-0">
                Search
              </button>
            </form>
            <button
              onClick={toggleUnreadOnly}
              className={`shrink-0 rounded-lg px-3 py-2 text-xs font-semibold ring-1 ring-inset transition ${
                unreadOnly
                  ? 'bg-accent-500/15 text-accent-300 ring-accent-500/30'
                  : 'bg-surface-800 text-zinc-300 ring-surface-600 hover:text-zinc-100'
              }`}
              title="Only messages without the UNREAD label"
            >
              {unreadOnly ? '✓ ' : ''}Unread only
            </button>
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              <span
                className={`inline-flex items-center gap-1.5 text-xs ${
                  checking ? 'text-accent-300' : 'text-zinc-500'
                }`}
              >
                {checking && <Spinner className="h-3 w-3" />}
                {checking
                  ? 'Checking mail…'
                  : checkedAt
                    ? `Checked ${formatRelative(checkedAt)}`
                    : 'Not checked yet'}
              </span>
              <button
                className="btn-secondary shrink-0 !px-3 !py-2 text-xs"
                onClick={handleCheckNow}
                disabled={checking || loadingList}
                title="Check for new messages now"
              >
                Check mail
              </button>
              <button
                className={`shrink-0 rounded-lg px-3 py-2 text-xs font-semibold ring-1 ring-inset transition ${
                  liveCheck
                    ? 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30'
                    : 'bg-surface-800 text-zinc-300 ring-surface-600 hover:text-zinc-100'
                }`}
                onClick={() => setLiveCheck((v) => !v)}
                title="Automatically check for new mail while this page is open"
              >
                {liveCheck ? 'Live: on' : 'Live: off'}
              </button>
            </div>
          </div>

          {/* ── workspace grid ─────────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[15rem_minmax(0,1fr)]">
            {/* labels rail */}
            <aside className="card h-fit p-2">
              <p className="px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
                Mailbox
              </p>
              <nav className="space-y-0.5">
                {railLabels.map((label) => (
                  <button
                    key={label.id}
                    onClick={() => openLabel(label.id)}
                    className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${
                      labelId === label.id && !search
                        ? 'bg-accent-500/10 font-medium text-accent-300 ring-1 ring-inset ring-accent-500/20'
                        : 'text-zinc-300 hover:bg-surface-800 hover:text-zinc-100'
                    }`}
                  >
                    <span
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                        label.id === 'INBOX'
                          ? 'bg-rose-400'
                          : label.id === 'STARRED'
                            ? 'bg-amber-400'
                            : label.id === 'SPAM'
                              ? 'bg-orange-400'
                              : 'bg-zinc-600'
                      }`}
                    />
                    <span className="truncate">{SYSTEM_LABEL_NAMES[label.id] || label.name}</span>
                    {label.id === 'INBOX' && label.messages_unread ? (
                      <CountBadge count={label.messages_unread} />
                    ) : null}
                  </button>
                ))}
              </nav>
              {userLabels.length > 0 && (
                <p className="px-2 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
                  Labels
                </p>
              )}
              {userLabels.map((label) => (
                <button
                  key={label.id}
                  onClick={() => openLabel(label.id)}
                  className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${
                    labelId === label.id && !search
                      ? 'bg-accent-500/10 font-medium text-accent-300 ring-1 ring-inset ring-accent-500/20'
                      : 'text-zinc-300 hover:bg-surface-800 hover:text-zinc-100'
                  }`}
                >
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-zinc-600" />
                  <span className="truncate">{label.name}</span>
                  {label.messages_unread ? <CountBadge count={label.messages_unread} /> : null}
                </button>
              ))}
              <button
                onClick={openAllMail}
                className={`mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${
                  !labelId && !search
                    ? 'bg-accent-500/10 font-medium text-accent-300 ring-1 ring-inset ring-accent-500/20'
                    : 'text-zinc-300 hover:bg-surface-800 hover:text-zinc-100'
                }`}
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-zinc-600" />
                All mail
              </button>

              <div className="mt-4 space-y-1 border-t border-surface-700 px-2 py-3 text-xs leading-relaxed text-zinc-500">
                {status.messages_total != null && (
                  <p>
                    <span className="font-semibold text-zinc-300">
                      {Number(status.messages_total).toLocaleString()}
                    </span>{' '}
                    messages total
                  </p>
                )}
                {unreadCount != null && (
                  <p>
                    <span className="font-semibold text-rose-300">{unreadCount}</span> unread in
                    inbox
                  </p>
                )}
              </div>
            </aside>

            {/* message list + thread pane */}
            <div className="grid min-w-0 grid-cols-1 gap-4 2xl:grid-cols-2">
              {/* list */}
              <div className="card flex min-w-0 flex-col overflow-hidden">
                <div className="flex items-center gap-2 border-b border-surface-700 px-4 py-3">
                  <h2 className="truncate text-sm font-semibold text-zinc-200">{viewTitle}</h2>
                  {selectedLabel?.messages_total != null && !unreadOnly && !search && (
                    <span className="text-xs tabular-nums text-zinc-500">
                      {selectedLabel.messages_total} messages
                    </span>
                  )}
                  {loadingList && <Spinner className="ml-auto h-4 w-4 text-zinc-500" />}
                </div>

                {listError ? (
                  <div className="flex flex-col items-center gap-3 p-8 text-center">
                    <p className="text-sm text-red-300">{listError}</p>
                    <button className="btn-secondary" onClick={refreshView}>
                      Retry
                    </button>
                  </div>
                ) : messages.length === 0 && !loadingList ? (
                  <div className="p-10 text-center text-sm text-zinc-500">
                    <p className="text-3xl">📭</p>
                    <p className="mt-2">
                      {unreadOnly
                        ? 'Nothing unread here — nice and quiet.'
                        : search
                          ? 'No messages match that search.'
                          : 'No messages here.'}
                    </p>
                  </div>
                ) : (
                  <ul className="scrollbar-thin max-h-[42rem] divide-y divide-surface-700 overflow-y-auto">
                    {messages.map((message) => (
                      <li key={message.id}>
                        <button
                          onClick={() => openThread(message.thread_id, message.id)}
                          className={`flex w-full items-start gap-3 px-4 py-3 text-left transition hover:bg-surface-800/60 ${
                            selectedThreadId === message.thread_id ? 'bg-surface-800/70' : ''
                          }`}
                        >
                          <Avatar name={message.from_name} email={message.from_email} />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-baseline gap-2">
                              <span
                                className={`truncate text-sm ${message.is_read ? 'text-zinc-300' : 'font-bold text-zinc-100'}`}
                              >
                                {message.from_name || message.from_email || 'Unknown sender'}
                              </span>
                              <span className="ml-auto shrink-0 text-[11px] tabular-nums text-zinc-500">
                                {formatRelative(message.internal_date)}
                              </span>
                            </div>
                            <p
                              className={`mt-0.5 truncate text-sm ${message.is_read ? 'text-zinc-400' : 'font-semibold text-zinc-200'}`}
                            >
                              {message.subject}
                            </p>
                            <p className="mt-0.5 truncate text-xs text-zinc-500">{message.snippet}</p>
                            <div className="mt-1.5 flex items-center gap-1.5">
                              {!message.is_read && (
                                <span className="h-2 w-2 shrink-0 rounded-full bg-accent-400" />
                              )}
                              {message.is_starred && <span className="text-xs text-amber-300">★</span>}
                              {(message.label_ids || [])
                                .filter((id) => !SYSTEM_LABELS.includes(id) && id !== 'UNREAD')
                                .slice(0, 2)
                                .map((id) => (
                                  <LabelChip key={id} label={labelByName[id] || { id, name: id }} />
                                ))}
                            </div>
                          </div>
                        </button>
                      </li>
                    ))}
                    {pageToken && (
                      <li>
                        <button
                          className="w-full px-4 py-3 text-center text-xs font-semibold text-accent-300 transition hover:bg-surface-800"
                          onClick={() =>
                            loadList({ label: labelId, q: search, unread: unreadOnly, token: pageToken })
                          }
                          disabled={loadingList}
                        >
                          {loadingList ? 'Loading…' : 'Load older messages'}
                        </button>
                      </li>
                    )}
                  </ul>
                )}
              </div>

              {/* thread reading pane */}
              <div className="card flex min-w-0 flex-col overflow-hidden">
                {!selectedThreadId ? (
                  <div className="flex h-full min-h-[20rem] flex-col items-center justify-center p-10 text-center text-sm text-zinc-500">
                    <p className="text-3xl">💬</p>
                    <p className="mt-2 max-w-xs">
                      Open a conversation to read the thread, reply, manage labels or download
                      attachments.
                    </p>
                  </div>
                ) : threadLoading ? (
                  <div className="flex h-full min-h-[20rem] items-center justify-center gap-3 text-zinc-400">
                    <Spinner className="h-5 w-5" /> Opening conversation…
                  </div>
                ) : !thread || thread.messages.length === 0 ? (
                  <div className="flex h-full min-h-[20rem] items-center justify-center text-sm text-zinc-500">
                    This conversation is empty or was deleted.
                  </div>
                ) : (
                  <div className="scrollbar-thin max-h-[42rem] min-h-[20rem] overflow-y-auto">
                    <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-surface-700 bg-surface-850/95 px-4 py-3 backdrop-blur">
                      <button
                        className="rounded-lg p-1.5 text-zinc-400 transition hover:bg-surface-800 hover:text-zinc-100 2xl:hidden"
                        onClick={() => {
                          setSelectedThreadId(null);
                          setThread(null);
                        }}
                        title="Back to the list"
                      >
                        ←
                      </button>
                      <h2 className="truncate text-sm font-semibold text-zinc-100">
                        {thread.messages[thread.messages.length - 1].subject}
                      </h2>
                      <span className="ml-auto shrink-0 text-xs text-zinc-500">
                        {thread.messages.length} message
                        {thread.messages.length === 1 ? '' : 's'}
                      </span>
                    </div>

                    <div className="space-y-5 p-4">
                      {thread.messages.map((message) => (
                        <MessageCard
                          key={message.id}
                          message={message}
                          labels={labels}
                          labelEditorMessageId={labelEditorMessageId}
                          setLabelEditorMessageId={setLabelEditorMessageId}
                          onReply={() => setCompose({ initial: buildReplyInitial(message) })}
                          onQuickAction={(add, remove) => quickAction(message, add, remove)}
                          onApplyLabels={(add, remove) => applyMessageLabels(message.id, add, remove)}
                          onDownload={downloadAttachment}
                        />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {/* ── compose modal (reply / new) ───────────────────────────────── */}
      {compose && (
        <Modal open title="New message" wide onClose={() => setCompose(null)}>
          <ComposeForm
            initial={compose.initial}
            onCancel={() => setCompose(null)}
            onSent={() => setCompose(null)}
          />
        </Modal>
      )}

      {/* ── disconnect confirm ────────────────────────────────────────── */}
      <Modal open={confirmDisconnect} title="Disconnect Gmail?" onClose={() => setConfirmDisconnect(false)}>
        <p className="text-sm text-zinc-400">
          LinkEasy will stop checking this mailbox and remove the stored connection. Messages stay
          in Gmail untouched.
        </p>
        <div className="mt-5 flex justify-end gap-3">
          <button className="btn-secondary" onClick={() => setConfirmDisconnect(false)}>
            Keep connected
          </button>
          <button className="btn-danger" onClick={handleDisconnect}>
            Disconnect
          </button>
        </div>
      </Modal>
    </div>
  );
}

/** One message inside the thread reading pane. */
function MessageCard({
  message,
  labels,
  labelEditorMessageId,
  setLabelEditorMessageId,
  onReply,
  onQuickAction,
  onApplyLabels,
  onDownload,
}) {
  const [showHtml, setShowHtml] = useState(false);
  const userLabels = labels.filter((l) => l.type === 'user');
  const editing = labelEditorMessageId === message.id;
  const [draft, setDraft] = useState([]);

  const sender = message.from_name
    ? `${message.from_name} <${message.from_email}>`
    : message.from_email || 'Unknown';
  const recipientLine = [message.to, message.cc]
    .filter(Boolean)
    .map((list) => list.map((a) => a.name || a.email).join(', '))
    .join('; ');

  function beginLabelEdit() {
    if (!editing) setDraft([...(message.label_ids || [])]);
    setLabelEditorMessageId(editing ? null : message.id);
  }

  async function saveLabels() {
    const current = new Set(message.label_ids || []);
    const wanted = new Set(draft);
    const add = [...wanted].filter((id) => !current.has(id));
    const remove = [...current].filter((id) => !wanted.has(id));
    if (add.length || remove.length) await onApplyLabels(add, remove);
    setLabelEditorMessageId(null);
  }

  const kb = (message.size_estimate || 0) / 1024;

  return (
    <article className="rounded-lg border border-surface-700 bg-surface-900/60 p-4">
      <div className="flex items-start gap-3">
        <Avatar name={message.from_name} email={message.from_email} className="h-9 w-9 text-xs" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-zinc-100">{sender}</p>
          <p className="truncate text-xs text-zinc-500">
            to {recipientLine || 'me'} · {formatDateTimeSafe(message.internal_date || message.date)}
          </p>
        </div>
        {!message.is_read && <span className="h-2 w-2 rounded-full bg-accent-400" title="Unread" />}
      </div>

      <div className="mt-3">
        {message.text_body ? (
          <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-zinc-200">
            {message.text_body}
          </pre>
        ) : (
          <p className="text-sm italic text-zinc-500">(no plain-text body)</p>
        )}
        {message.html_body && (
          <div className="mt-2">
            {!showHtml ? (
              <button
                className="text-xs font-semibold text-accent-300 hover:text-accent-200"
                onClick={() => setShowHtml(true)}
              >
                Show HTML version
              </button>
            ) : (
              <div className="overflow-hidden rounded-lg border border-surface-700">
                {/* sandboxed: no scripts, no same-origin — HTML from strangers
                    must never run or read the app's storage. */}
                <iframe
                  sandbox=""
                  srcDoc={`<html><body>${message.html_body}</body></html>`}
                  title="HTML email body"
                  className="h-72 w-full bg-white text-black"
                />
              </div>
            )}
          </div>
        )}
      </div>

      {message.attachments?.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2 border-t border-surface-700 pt-3">
          {message.attachments.map((att) => (
            <button
              key={att.attachment_id}
              onClick={() => onDownload(message.id, att)}
              className="group inline-flex max-w-full items-center gap-2 rounded-lg border border-surface-600 bg-surface-800 px-3 py-1.5 text-xs text-zinc-300 transition hover:border-accent-500/40 hover:text-accent-200"
              title="Download"
            >
              <span aria-hidden>📎</span>
              <span className="max-w-[14rem] truncate font-medium">{att.filename}</span>
              <span className="tabular-nums text-zinc-500">{kb > 0 ? `${kb.toFixed(0)} KB` : ''}</span>
            </button>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-surface-700 pt-3">
        <ActionButton label="Reply" onClick={onReply} />
        {message.is_read ? (
          <ActionButton label="Mark unread" onClick={() => onQuickAction([], ['UNREAD'])} />
        ) : (
          <ActionButton label="Mark read" onClick={() => onQuickAction(['UNREAD'], [])} />
        )}
        <ActionButton
          label={message.is_starred ? 'Unstar' : 'Star'}
          onClick={() =>
            onQuickAction(message.is_starred ? [] : ['STARRED'], message.is_starred ? ['STARRED'] : [])
          }
        />
        <ActionButton label="Archive" onClick={() => onQuickAction([], ['INBOX'])} />
        <ActionButton label="Trash" onClick={() => onQuickAction(['TRASH'], [])} />
        <button
          className="rounded-lg px-2.5 py-1.5 text-xs font-semibold text-zinc-400 transition hover:bg-surface-800 hover:text-zinc-100"
          onClick={beginLabelEdit}
        >
          Labels
        </button>
      </div>

      {editing && (
        <div className="mt-3 rounded-lg border border-surface-600 bg-surface-800/60 p-3">
          <div className="grid max-h-40 grid-cols-1 gap-1 overflow-y-auto sm:grid-cols-2">
            {userLabels.map((label) => (
              <label key={label.id} className="flex cursor-pointer items-center gap-2 text-sm text-zinc-300">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-surface-600 accent-teal-400"
                  checked={draft.includes(label.id)}
                  onChange={(e) =>
                    setDraft((d) => (e.target.checked ? [...d, label.id] : d.filter((x) => x !== label.id)))
                  }
                />
                <span className="truncate">{label.name}</span>
              </label>
            ))}
            {userLabels.length === 0 && (
              <p className="text-xs text-zinc-500">No custom labels on this mailbox yet.</p>
            )}
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <button className="btn-secondary !px-3 !py-1.5 text-xs" onClick={() => setLabelEditorMessageId(null)}>
              Cancel
            </button>
            <button className="btn-primary !px-3 !py-1.5 text-xs" onClick={saveLabels}>
              Apply labels
            </button>
          </div>
        </div>
      )}
    </article>
  );
}

function ActionButton({ label, onClick }) {
  return (
    <button
      className="rounded-lg px-2.5 py-1.5 text-xs font-semibold text-zinc-400 transition hover:bg-surface-800 hover:text-zinc-100"
      onClick={onClick}
    >
      {label}
    </button>
  );
}
