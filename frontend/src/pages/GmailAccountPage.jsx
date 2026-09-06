import { useCallback, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { gmailApi } from '../api/gmail';
import { getErrorMessage } from '../api/client';
import { GmailMark, GmailStatusBadge } from '../components/gmail/GmailBits';
import Modal from '../components/Modal';
import { Spinner } from '../components/Spinner';

/** Gmail sign-in and disconnect live under Accounts, not inside the mailbox. */
export default function GmailAccountPage() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
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
    setBusy(true);
    try {
      await gmailApi.disconnect();
      setConfirmDisconnect(false);
      toast.success('Gmail disconnected');
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not disconnect Gmail'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl">
      <Link to="/app/account" className="text-sm text-zinc-400 hover:text-accent-300">← Accounts</Link>
      <div className="mt-4 flex items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-rose-500/10 text-rose-300"><GmailMark className="h-6 w-6" /></div>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-accent-400">Main accounts</p>
          <h1 className="text-2xl font-bold text-zinc-100">Gmail connection</h1>
        </div>
      </div>
      <p className="mt-3 text-sm text-zinc-400">Manage the Gmail or Google Workspace mailbox you read and reply from.</p>

      <section className="card mt-6 p-6 sm:p-8">
        {loading ? (
          <div className="flex h-32 items-center justify-center gap-2 text-sm text-zinc-400" role="status"><Spinner /> Loading connection…</div>
        ) : error ? (
          <div role="alert">
            <p className="text-sm text-red-300">{error}</p>
            <button type="button" className="btn-secondary mt-4" onClick={load}>Retry</button>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <h2 className="min-w-0 break-all text-lg font-semibold text-zinc-100">{status?.connected ? status.account_email : 'Connect your Gmail'}</h2>
              <GmailStatusBadge status={status} />
            </div>
            {status?.connected ? (
              <>
                <p className="mt-3 text-sm text-zinc-400">Your mailbox is connected. Open Gmail to read messages, check for new mail and send replies.</p>
                {status.reconnect_required && <p className="mt-3 text-sm text-amber-300">Google access needs to be renewed. Reconnect before using your inbox.</p>}
                <div className="mt-6 flex flex-wrap gap-3 border-t border-surface-700 pt-5">
                  <Link to="/app/gmail" className="btn-primary">Open Gmail inbox</Link>
                  <button type="button" className="btn-secondary" onClick={connect} disabled={busy || !status.configured}>{busy && <Spinner />}Reconnect Gmail</button>
                  <button type="button" className="btn-danger" onClick={() => setConfirmDisconnect(true)} disabled={busy}>Disconnect</button>
                </div>
              </>
            ) : status?.configured ? (
              <>
                <p className="mt-3 text-sm leading-relaxed text-zinc-400">LinkEasy uses Google's official sign-in. Personal @gmail.com accounts and Google Workspace mailboxes are supported.</p>
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
              <p className="mt-3 text-sm leading-relaxed text-zinc-400">Gmail needs a setup step first. Ask the operator to configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (see docs/gmail_setup.md). Sign-in will be available after setup.</p>
            )}
            <p className="mt-5 text-xs leading-relaxed text-zinc-500">Tokens are stored encrypted. Disconnecting removes LinkEasy's stored connection; it does not delete any messages from Gmail.</p>
          </>
        )}
      </section>
      <Modal open={confirmDisconnect} title="Disconnect Gmail?" onClose={() => !busy && setConfirmDisconnect(false)}>
        <p className="text-sm text-zinc-400">LinkEasy will stop accessing this mailbox and remove the stored connection. Messages stay in Gmail untouched.</p>
        <div className="mt-5 flex justify-end gap-3">
          <button type="button" className="btn-secondary" onClick={() => setConfirmDisconnect(false)} disabled={busy}>Keep connected</button>
          <button type="button" className="btn-danger" onClick={disconnect} disabled={busy}>{busy && <Spinner />}Disconnect</button>
        </div>
      </Modal>
    </div>
  );
}
