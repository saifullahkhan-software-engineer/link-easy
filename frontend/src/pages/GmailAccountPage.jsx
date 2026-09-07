import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { gmailApi } from '../api/gmail';
import { getErrorMessage } from '../api/client';
import { GmailMark } from '../components/gmail/GmailBits';
import Modal from '../components/Modal';
import { Spinner } from '../components/Spinner';
import {
  AddAccountCard,
  ConnectedAccountCard,
  ConnectionStatusPill,
  GmailGlyph,
  connectionCountLabel,
} from '../components/accounts/AccountCards';

function formatDate(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/**
 * Manage Gmail: one card per connected mailbox plus "Connect another mailbox".
 * The Accounts hub only shows how many mailboxes are connected.
 */
export default function GmailAccountPage() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(null);
  const [searchParams, setSearchParams] = useSearchParams();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await gmailApi.status();
      setStatus(data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not load the Gmail connection'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const connected = searchParams.get('connected');
    const oauthError = searchParams.get('error');
    if (connected !== '1' && !oauthError) return;
    if (connected === '1') toast.success('Gmail connected');
    if (oauthError) toast.error(oauthError);
    const remaining = new URLSearchParams(searchParams);
    remaining.delete('connected');
    remaining.delete('error');
    setSearchParams(remaining, { replace: true });
  }, [searchParams, setSearchParams]);

  const connect = async () => {
    setBusy(true);
    try {
      const { data } = await gmailApi.authUrl();
      window.location.assign(data.auth_url);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not start Google sign-in'));
      setBusy(false);
    }
  };

  const disconnect = async () => {
    const target = confirmDisconnect;
    if (!target) return;
    setBusy(true);
    try {
      await gmailApi.disconnect(target.accountId || null);
      setConfirmDisconnect(null);
      toast.success('Gmail disconnected');
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not disconnect Gmail'));
    } finally {
      setBusy(false);
    }
  };

  const mailboxes = useMemo(() => {
    if (Array.isArray(status?.accounts) && status.accounts.length) return status.accounts;
    if (status?.connected) {
      return [{
        id: null,
        account_email: status.account_email,
        reconnect_required: status.reconnect_required,
        last_checked_at: status.last_checked_at,
        expires_at: status.expires_at,
      }];
    }
    return [];
  }, [status]);

  const configured = Boolean(status?.configured);

  return (
    <div className="mx-auto max-w-5xl">
      <Link to="/app/account" className="btn-secondary text-xs" data-testid="back-to-accounts">← Accounts</Link>

      <header className="mt-4 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-accent-400">Main accounts</p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-rose-500/10 text-rose-300">
              <GmailGlyph />
            </span>
            <h1 className="text-2xl font-bold text-zinc-100">Gmail connection</h1>
            {!loading && <ConnectionStatusPill state={mailboxes.length ? (mailboxes.some((m) => m.reconnect_required) ? 'attention' : 'connected') : 'none'} />}
          </div>
          <p className="mt-2 text-sm text-zinc-400">
            The Gmail or Google Workspace mailboxes you read and reply from. Tokens are stored encrypted.
          </p>
        </div>
        {mailboxes.length > 0 && configured && (
          <button type="button" className="btn-primary" onClick={connect} disabled={busy} data-testid="gmail-connect-another">
            {busy && <Spinner />}
            Connect another mailbox
          </button>
        )}
      </header>

      {loading ? (
        <div className="flex h-32 items-center justify-center gap-2 text-sm text-zinc-400" role="status"><Spinner /> Loading connection…</div>
      ) : error ? (
        <div className="card mt-6 p-6" role="alert">
          <p className="text-sm text-red-300">{error}</p>
          <button type="button" className="btn-secondary mt-4" onClick={load}>Retry</button>
        </div>
      ) : (
        <section className="mt-6" aria-label="Connected Gmail mailboxes">
          <h2 className="text-sm font-semibold text-zinc-300">
            {connectionCountLabel(mailboxes.length, { one: 'mailbox', many: 'mailboxes' })}
          </h2>

          {mailboxes.length === 0 ? (
            <div className="card mt-4 p-6">
              {configured ? (
                <>
                  <h3 className="text-base font-semibold text-zinc-100">Connect your Gmail</h3>
                  <p className="mt-2 text-sm leading-relaxed text-zinc-400">
                    LinkEasy uses Google&apos;s official sign-in. Personal @gmail.com accounts and Google Workspace mailboxes are supported.
                  </p>
                  <ul className="mt-4 list-disc space-y-2 pl-5 text-sm text-zinc-300">
                    <li>Read and search messages and conversations.</li>
                    <li>Manage labels, read status, archive and trash.</li>
                    <li>Compose messages and send replies.</li>
                  </ul>
                  <button type="button" className="btn-primary mt-6" onClick={connect} disabled={busy}>
                    {busy ? <Spinner /> : <GmailMark />}{busy ? 'Opening Google…' : 'Connect Gmail'}
                  </button>
                </>
              ) : (
                <p className="text-sm leading-relaxed text-zinc-400">
                  Gmail needs a setup step first. Ask the operator to configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
                  (see docs/gmail_setup.md). Sign-in will be available after setup.
                </p>
              )}
            </div>
          ) : (
            <>
              <div className="mt-4 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
                {mailboxes.map((mailbox) => (
                  <ConnectedAccountCard
                    key={mailbox.id || mailbox.account_email}
                    testId={`gmail-mailbox-${mailbox.id || mailbox.account_email}`}
                    icon={<GmailGlyph />}
                    iconClass="bg-rose-500/10 text-rose-300"
                    title={mailbox.account_email}
                    subtitle={mailbox.reconnect_required ? 'Google access needs renewing' : 'Connected mailbox'}
                    badge={<ConnectionStatusPill state={mailbox.reconnect_required ? 'attention' : 'connected'} />}
                    details={[
                      { label: 'Last checked', value: formatDate(mailbox.last_checked_at) },
                      { label: 'Token expires', value: formatDate(mailbox.expires_at) },
                    ]}
                    actions={
                      <>
                        {mailbox.reconnect_required ? (
                          <button type="button" className="btn-primary text-xs" onClick={connect} disabled={busy}>
                            {busy && <Spinner />}
                            Reconnect Gmail
                          </button>
                        ) : (
                          <Link to="/app/gmail" className="btn-secondary text-xs">Open inbox</Link>
                        )}
                        <button
                          type="button"
                          className="btn-danger text-xs"
                          onClick={() => setConfirmDisconnect({ accountId: mailbox.id, email: mailbox.account_email })}
                          disabled={busy}
                          data-testid={`disconnect-mailbox-${mailbox.id || 'default'}`}
                        >
                          Disconnect
                        </button>
                      </>
                    }
                  />
                ))}

                {configured && (
                  <AddAccountCard
                    label="Connect another mailbox"
                    hint="Sign in with a second Google account."
                    onClick={connect}
                    disabled={busy}
                    testId="add-gmail-mailbox"
                  />
                )}
              </div>

              <div className="mt-6 flex flex-wrap gap-3">
                <Link to="/app/gmail" className="btn-primary">Open Gmail inbox</Link>
                <Link to="/app/gmail/compose" className="btn-secondary">Compose a message</Link>
              </div>
            </>
          )}

          <p className="mt-6 text-xs leading-relaxed text-zinc-500">
            Disconnecting removes LinkEasy&apos;s stored connection; it does not delete any messages from Gmail.
          </p>
        </section>
      )}

      <Modal
        open={Boolean(confirmDisconnect)}
        title={confirmDisconnect?.email ? `Disconnect ${confirmDisconnect.email}?` : 'Disconnect Gmail?'}
        onClose={() => !busy && setConfirmDisconnect(null)}
      >
        <p className="text-sm text-zinc-400">
          LinkEasy will stop accessing{' '}
          {confirmDisconnect?.email ? (
            <span className="font-medium text-zinc-200">{confirmDisconnect.email}</span>
          ) : (
            'this mailbox'
          )}{' '}
          and remove the stored connection. Messages stay in Gmail untouched. Your other connected mailboxes are unaffected.
        </p>
        <div className="mt-5 flex justify-end gap-3">
          <button type="button" className="btn-secondary" onClick={() => setConfirmDisconnect(null)} disabled={busy}>Keep connected</button>
          <button type="button" className="btn-danger" onClick={disconnect} disabled={busy}>{busy && <Spinner />}Disconnect</button>
        </div>
      </Modal>
    </div>
  );
}
