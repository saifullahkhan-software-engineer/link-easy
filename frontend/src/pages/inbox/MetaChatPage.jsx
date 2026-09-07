import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { inboxApi } from '../../api/inbox';
import { socialSchedulerApi } from '../../api/socialScheduler';
import { getErrorMessage } from '../../api/client';
import { INBOX_CHANNELS } from '../../constants/inbox';
import { ChannelIcon, InboxPageHeader } from '../../components/inbox/InboxBits';
import AccountPicker from '../../components/accounts/AccountPicker';
import { useStoredAccountId } from '../../hooks/useStoredAccountId';
import { Spinner } from '../../components/Spinner';
import { formatDateTime } from '../../components/social/SocialBits';

/** A text-only inbox for the user's connected Facebook Page / professional Instagram. */
export default function MetaChatPage({ channel }) {
  const meta = INBOX_CHANNELS.find((item) => item.id === channel);
  const platformName = channel === 'instagram' ? 'Instagram' : 'Facebook Page';
  const [connection, setConnection] = useState(null);
  const [connectionLoading, setConnectionLoading] = useState(true);
  const [connectionError, setConnectionError] = useState(null);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [conversations, setConversations] = useState([]);
  const [nextCursor, setNextCursor] = useState(null);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState(null);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState(null);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const listRequest = useRef(null);
  const messagesRequest = useRef(null);
  const activeConversation = useRef(null);
  const sendInFlight = useRef(false);
  const messagesEnd = useRef(null);
  const available = Boolean(connection?.connected && !connection?.reconnect_required);

  // Multi-account: pick which connected Facebook Page / Instagram account to
  // read and reply from for this channel.
  const [accounts, setAccounts] = useState([]);
  const [selectedAccountId, setSelectedAccountId] = useStoredAccountId(`meta-inbox:${channel}`);
  const accountRef = useRef('');
  useEffect(() => {
    accountRef.current = selectedAccountId;
  }, [selectedAccountId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await inboxApi.accounts(channel);
        const list = Array.isArray(data?.accounts) ? data.accounts : [];
        if (cancelled) return;
        setAccounts(list);
        const defaultId = list.find((a) => a.is_default)?.id ?? list[0]?.id ?? '';
        if (!accountRef.current && defaultId) setSelectedAccountId(String(defaultId));
      } catch {
        if (!cancelled) setAccounts([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [channel]);

  useEffect(() => {
    let cancelled = false;
    setConnectionLoading(true);
    setConnectionError(null);
    socialSchedulerApi.listPlatforms().then(({ data }) => {
      if (!cancelled) setConnection((Array.isArray(data) ? data : []).find((item) => item.platform === meta.platform) || null);
    }).catch((err) => {
      if (!cancelled) setConnectionError(getErrorMessage(err, 'Could not load your connection'));
    }).finally(() => {
      if (!cancelled) setConnectionLoading(false);
    });
    return () => { cancelled = true; };
  }, [meta.platform, connectionAttempt]);

  const loadConversations = useCallback(async (after) => {
    listRequest.current?.abort();
    const request = new AbortController();
    listRequest.current = request;
    setListLoading(true);
    setListError(null);
    try {
      const { data } = await inboxApi.conversations(channel, { after, signal: request.signal, accountId: accountRef.current || null });
      if (request.signal.aborted) return;
      const rows = Array.isArray(data.conversations) ? data.conversations : [];
      setConversations((previous) => after
        ? [...new Map([...previous, ...rows].map((row) => [row.id, row])).values()]
        : rows);
      setNextCursor(data.next_cursor || null);
    } catch (err) {
      if (!request.signal.aborted) setListError(getErrorMessage(err, 'Could not load conversations'));
    } finally {
      if (!request.signal.aborted) setListLoading(false);
    }
  }, [channel]);

  const loadMessages = useCallback(async (conversationId) => {
    messagesRequest.current?.abort();
    const request = new AbortController();
    messagesRequest.current = request;
    setMessagesLoading(true);
    setMessagesError(null);
    try {
      const { data } = await inboxApi.messages(channel, conversationId, { signal: request.signal, accountId: accountRef.current || null });
      if (!request.signal.aborted && activeConversation.current === conversationId) {
        setMessages(Array.isArray(data.messages) ? data.messages : []);
      }
    } catch (err) {
      if (!request.signal.aborted) setMessagesError(getErrorMessage(err, 'Could not load messages'));
    } finally {
      if (!request.signal.aborted) setMessagesLoading(false);
    }
  }, [channel]);

  useEffect(() => {
    if (available) loadConversations();
    return () => { listRequest.current?.abort(); };
  }, [available, loadConversations]);

  // Refresh while the page is open so newly received conversations appear
  // without requiring the user to leave and re-enter the inbox.
  useEffect(() => {
    if (!available) return undefined;
    const timer = setInterval(() => loadConversations(), 15_000);
    return () => clearInterval(timer);
  }, [available, loadConversations]);

  useEffect(() => {
    if (selected) loadMessages(selected.id);
    return () => { messagesRequest.current?.abort(); };
  }, [selected, loadMessages]);

  useEffect(() => {
    if (!available || !selected) return undefined;
    const timer = setInterval(() => loadMessages(selected.id), 10_000);
    return () => clearInterval(timer);
  }, [available, selected, loadMessages]);

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ block: 'nearest' });
  }, [messages]);

  useEffect(() => () => { activeConversation.current = null; }, []);

  function selectConversation(conversation) {
    if (sendInFlight.current || activeConversation.current === (conversation?.id || null)) return;
    activeConversation.current = conversation?.id || null;
    messagesRequest.current?.abort();
    setSelected(conversation);
    setMessages([]);
    setMessagesError(null);
    setMessagesLoading(Boolean(conversation));
    setDraft('');
  }

  function refresh() {
    loadConversations();
    if (selected) loadMessages(selected.id);
  }

  const draftBytes = new TextEncoder().encode(draft.trim()).length;

  async function sendReply(event) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !selected || sending || sendInFlight.current || messagesLoading || messagesError || draftBytes > 1000) return;
    const conversationId = selected.id;
    sendInFlight.current = true;
    setSending(true);
    try {
      const { data } = await inboxApi.reply(channel, conversationId, text, accountRef.current || null);
      if (activeConversation.current !== conversationId) return;
      // Display only a provider-acknowledged send, never an optimistic/fake reply.
      const createdAt = new Date().toISOString();
      setMessages((previous) => [...previous, { id: data.message_id, text, outgoing: true, sender: 'You', created_at: createdAt }].slice(-20));
      setConversations((previous) => previous.map((row) => row.id === conversationId ? { ...row, preview: text, updated_at: createdAt } : row));
      setDraft('');
    } catch (err) {
      if (activeConversation.current === conversationId) toast.error(getErrorMessage(err, 'Could not send your reply. Refresh the conversation before trying again.'));
    } finally {
      sendInFlight.current = false;
      if (activeConversation.current === conversationId) setSending(false);
    }
  }

  const visibleConversations = conversations.filter((row) => `${row.name} ${row.preview}`.toLowerCase().includes(search.trim().toLowerCase()));

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <InboxPageHeader
        channel={channel}
        description={channel === 'instagram' ? 'Read and reply to conversations on your connected professional Instagram account.' : 'Read and reply to Messenger conversations on your connected Facebook Page.'}
        action={available && (<>
          {accounts.length > 1 && (
            <AccountPicker
              id={`meta-${channel}-account`}
              hideLabel
              accounts={accounts}
              value={selectedAccountId}
              onChange={setSelectedAccountId}
              getKey={(a) => String(a.id)}
              getLabel={(a) => a.account_name || a.account_id}
              placeholder="Account"
            />
          )}
          <button type="button" className="btn-secondary" onClick={refresh} disabled={listLoading || messagesLoading || sending}>{listLoading && <Spinner />}Refresh inbox</button>
        </>)}
      />

      {connectionLoading ? (
        <div className="card flex h-64 items-center justify-center gap-2 text-sm text-zinc-400" role="status"><Spinner /> Checking connection…</div>
      ) : connectionError ? (
        <div className="card p-8" role="alert">
          <p className="text-sm text-red-300">{connectionError}</p>
          <button type="button" className="btn-secondary mt-4" onClick={() => setConnectionAttempt((value) => value + 1)}>Retry connection</button>
        </div>
      ) : !available ? (
        <section className="card mx-auto max-w-2xl px-6 py-12 text-center">
          <ChannelIcon channel={channel} className="mx-auto h-16 w-16 rounded-2xl bg-accent-500/10 text-accent-300" />
          <h2 className="mt-5 text-xl font-semibold text-zinc-100">{connection?.reconnect_required ? `Reconnect ${platformName}` : `Connect ${platformName} to get started`}</h2>
          <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-zinc-400">
            {channel === 'instagram' ? 'Use a Business or Creator Instagram account linked to a Facebook Page.' : 'Messenger Chat uses your Facebook Page connection, not a personal Facebook inbox.'}
            {' '}Manage the connection in Accounts → Socials and approve messaging permissions.
          </p>
          {connection?.configured === false && <p className="mt-3 text-xs text-amber-300">The operator needs to configure this platform before you can connect.</p>}
          <Link to={`/app/account/social/${meta.platform}`} className="btn-primary mt-6">Go to social accounts</Link>
        </section>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-500">
            <p>Account: <span className="font-medium text-zinc-300">{accounts.find((a) => a.id === selectedAccountId)?.account_name || connection.account_name || connection.account_id}</span></p>
            <p>Latest 20 messages per conversation · Refresh to check for updates</p>
          </div>
          {listError && <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4" role="alert">
            <p className="text-sm text-amber-200">{listError}</p>
            <div className="mt-3 flex flex-wrap gap-3"><button type="button" className="btn-secondary" onClick={() => loadConversations()} disabled={listLoading}>Retry conversations</button><Link to={`/app/account/social/${meta.platform}`} className="btn-secondary">Check social connection</Link></div>
          </div>}
          <div className="card flex h-[34rem] overflow-hidden p-0">
            <aside className={`${selected ? 'hidden md:flex' : 'flex'} w-full shrink-0 flex-col border-r border-surface-700 md:w-72`}>
              <div className="border-b border-surface-700 p-3">
                <label htmlFor={`${channel}-search`} className="mb-2 block text-xs font-medium text-zinc-400">Conversations</label>
                <input id={`${channel}-search`} type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search loaded conversations…" className="input-field" />
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
                {listLoading && conversations.length === 0 ? (
                  <div className="flex justify-center gap-2 p-8 text-sm text-zinc-500" role="status"><Spinner /> Loading chats…</div>
                ) : visibleConversations.length === 0 ? (
                  <div className="px-5 py-10 text-center text-sm text-zinc-500">
                    <p className="font-medium text-zinc-300">{listError ? 'Conversations unavailable' : search ? 'No matching conversations' : 'No conversations returned by Meta'}</p>
                    <p className="mt-2 text-xs leading-relaxed">{search ? 'Try another search or load more conversations.' : 'Send a test message to this Facebook Page or professional Instagram account, then refresh. Meta only returns conversations available to the connected Page token and approved messaging permissions.'}</p>
                  </div>
                ) : (
                  <ul>
                    {visibleConversations.map((conversation) => (
                      <li key={conversation.id}>
                        <button type="button" onClick={() => selectConversation(conversation)} disabled={sending} aria-pressed={selected?.id === conversation.id} className={`w-full border-b border-surface-700 px-4 py-4 text-left transition hover:bg-surface-800 disabled:opacity-50 ${selected?.id === conversation.id ? 'bg-accent-500/10' : ''}`}>
                          <p className="truncate text-sm font-medium text-zinc-100">{conversation.name}</p>
                          <p className="mt-1 truncate text-xs text-zinc-500">{conversation.preview || 'Open conversation'}</p>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {nextCursor && <button type="button" className="w-full px-4 py-3 text-sm font-medium text-accent-300 disabled:opacity-50" onClick={() => loadConversations(nextCursor)} disabled={listLoading}>{listLoading ? 'Loading…' : 'Load more conversations'}</button>}
              </div>
            </aside>
            <section className={`${selected ? 'flex' : 'hidden md:flex'} min-w-0 flex-1 flex-col`} aria-label="Chat messages">
              {!selected ? (
                <div className="flex flex-1 flex-col items-center justify-center px-8 text-center">
                  <ChannelIcon channel={channel} />
                  <h2 className="mt-4 text-base font-semibold text-zinc-200">Select a conversation</h2>
                  <p className="mt-2 text-sm text-zinc-500">Open a chat to read messages and send a text reply.</p>
                </div>
              ) : (
                <>
                  <header className="flex items-center gap-3 border-b border-surface-700 px-4 py-3">
                    <button type="button" onClick={() => selectConversation(null)} disabled={sending} aria-label="Back to conversations" className="rounded-lg px-2 py-1 text-zinc-400 hover:bg-surface-700">←</button>
                    <h2 className="truncate text-sm font-semibold text-zinc-100">{selected.name}</h2>
                    {messagesLoading && <Spinner className="ml-auto h-4 w-4 text-zinc-500" />}
                  </header>
                  <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 scrollbar-thin">
                    {messagesError ? (
                      <div role="alert" className="text-center"><p className="text-sm text-red-300">{messagesError}</p><button type="button" className="btn-secondary mt-3" onClick={() => loadMessages(selected.id)}>Retry messages</button></div>
                    ) : messages.length === 0 ? (
                      <p className="py-8 text-center text-sm text-zinc-500">{messagesLoading ? 'Loading messages…' : 'No messages available in this conversation.'}</p>
                    ) : messages.map((message) => (
                      <div key={message.id} className={`flex ${message.outgoing ? 'justify-end' : 'justify-start'}`}>
                        <div className={`max-w-[85%] rounded-2xl px-4 py-2.5 ${message.outgoing ? 'rounded-br-sm bg-accent-500/15 text-accent-100' : 'rounded-bl-sm bg-surface-800 text-zinc-200'}`}>
                          {!message.outgoing && <p className="mb-1 text-xs font-medium text-zinc-400">{message.sender}</p>}
                          <p className="whitespace-pre-wrap break-words text-sm">{message.text || 'Attachment or non-text message — view it in the original app.'}</p>
                          <p className="mt-1 text-right text-[10px] text-zinc-500">{formatDateTime(message.created_at)}</p>
                        </div>
                      </div>
                    ))}
                    <div ref={messagesEnd} />
                  </div>
                  <form onSubmit={sendReply} className="border-t border-surface-700 p-3">
                    <div className="flex items-end gap-2">
                      <label className="min-w-0 flex-1"><span className="sr-only">Message</span><textarea value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Write a reply…" rows={2} maxLength={1000} disabled={sending || messagesLoading || Boolean(messagesError)} className="input-field resize-none" /></label>
                      <button type="submit" className="btn-primary mb-1" disabled={sending || messagesLoading || Boolean(messagesError) || !draft.trim() || draftBytes > 1000}>{sending && <Spinner />}{sending ? 'Sending…' : 'Send reply'}</button>
                    </div>
                    <p className={`mt-2 text-[11px] ${draftBytes > 1000 ? 'text-red-300' : 'text-zinc-500'}`}>{draftBytes > 1000 ? 'Shorten your reply to 1,000 UTF-8 bytes or fewer.' : "Text replies only. Meta's messaging permissions and reply window apply."}</p>
                  </form>
                </>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
