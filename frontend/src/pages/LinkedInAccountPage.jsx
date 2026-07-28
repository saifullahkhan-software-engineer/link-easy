import { useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { linkedinApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { AccountStatusBadge } from '../components/Badge';
import Modal from '../components/Modal';
import VerificationCodeModal from '../components/VerificationCodeModal';
import { SlowOperationNotice, Spinner } from '../components/Spinner';

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/* ------------------------- edit account modal ---------------------------- */
function EditAccountModal({ open, account, onClose, onSaved }) {
  const [form, setForm] = useState({ label: '', linkedin_email: '', linkedin_password: '' });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open && account) {
      setForm({ label: account.label || '', linkedin_email: account.linkedin_email || '', linkedin_password: '' });
    }
  }, [open, account]);

  async function save(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const payload = {};
      if (form.label.trim() !== (account.label || '')) payload.label = form.label.trim(); // '' clears the label
      if (form.linkedin_email.trim() && form.linkedin_email.trim() !== account.linkedin_email)
        payload.linkedin_email = form.linkedin_email.trim();
      if (form.linkedin_password) payload.linkedin_password = form.linkedin_password;
      if (Object.keys(payload).length === 0) {
        onClose();
        return;
      }
      const { data } = await linkedinApi.updateAccount(payload);
      toast.success('Account updated.');
      if (payload.linkedin_password)
        toast('Password changed — the session will need re-verification.', { icon: 'ℹ️' });
      onSaved(data);
      onClose();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not update the account.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={busy ? undefined : onClose} title="Edit LinkedIn account">
      <form onSubmit={save} className="space-y-4">
        <div>
          <label className="input-label" htmlFor="edit-label">Label</label>
          <input
            id="edit-label"
            className="input-field"
            value={form.label}
            onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
            placeholder="Work account"
          />
        </div>
        <div>
          <label className="input-label" htmlFor="edit-email">LinkedIn email</label>
          <input
            id="edit-email"
            type="email"
            className="input-field"
            value={form.linkedin_email}
            onChange={(e) => setForm((f) => ({ ...f, linkedin_email: e.target.value }))}
          />
        </div>
        <div>
          <label className="input-label" htmlFor="edit-password">New LinkedIn password</label>
          <input
            id="edit-password"
            type="password"
            className="input-field"
            value={form.linkedin_password}
            onChange={(e) => setForm((f) => ({ ...f, linkedin_password: e.target.value }))}
            placeholder="Leave blank to keep current password"
            autoComplete="new-password"
          />
          <p className="mt-1 text-xs text-zinc-500">
            Managed credentials are AES-256 encrypted at rest and never shown back to you.
          </p>
        </div>
        <div className="flex justify-end gap-3">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={busy}>
            {busy && <Spinner />}
            Save changes
          </button>
        </div>
      </form>
    </Modal>
  );
}

/* ------------------------------- main page ------------------------------- */
export default function LinkedInAccountPage() {
  const { email: ownerEmail } = useAuth();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [account, setAccount] = useState(null);
  const [notConnected, setNotConnected] = useState(false);

  const [form, setForm] = useState({ linkedin_email: '', linkedin_password: '', label: '' });
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef(null);

  const [verification, setVerification] = useState({ open: false, sessionId: null });
  const [refreshing, setRefreshing] = useState(false);
  const [refreshElapsed, setRefreshElapsed] = useState(0);
  const [editOpen, setEditOpen] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  const fetchAccount = useCallback(async () => {
    setLoadError(null);
    try {
      const { data } = await linkedinApi.getAccount();
      setAccount(data);
      setNotConnected(false);
    } catch (err) {
      if (err?.response?.status === 404) {
        setAccount(null);
        setNotConnected(true);
      } else {
        setAccount(null);
        setNotConnected(false);
        setLoadError(getErrorMessage(err, 'Could not load your LinkedIn account.'));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAccount();
  }, [fetchAccount]);

  // elapsed timers for slow operations
  useEffect(() => {
    if (connecting) {
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
      return () => clearInterval(timerRef.current);
    }
  }, [connecting]);

  useEffect(() => {
    if (refreshing) {
      setRefreshElapsed(0);
      const t = setInterval(() => setRefreshElapsed((s) => s + 1), 1000);
      return () => clearInterval(t);
    }
  }, [refreshing]);

  /* ------------------------------ connect ------------------------------ */
  async function connect(e) {
    e.preventDefault();
    setConnecting(true);
    setConnectError(null);
    try {
      const { data } = await linkedinApi.connect({
        owner_email: ownerEmail,
        linkedin_email: form.linkedin_email.trim(),
        linkedin_password: form.linkedin_password,
        label: form.label.trim() || undefined,
      });

      if (data.status === 'LOGIN_SUCCESS') {
        toast.success('LinkedIn account connected.');
        setAccount(data.account);
        setNotConnected(false);
        setForm({ linkedin_email: '', linkedin_password: '', label: '' });
      } else if (data.status === 'PENDING_VERIFICATION') {
        toast('LinkedIn wants a verification code — check the linked email/device.', {
          icon: '🔐',
          duration: 5000,
        });
        setVerification({ open: true, sessionId: data.session_id });
      } else {
        setConnectError(data.message || 'Unexpected response from the server.');
      }
    } catch (err) {
      // 400 — login failed outright (bad creds, checkpoint, bot detection)
      setConnectError(getErrorMessage(err, 'LinkedIn login failed.'));
    } finally {
      setConnecting(false);
    }
  }

  /* --------------------------- refresh session -------------------------- */
  async function refreshSession() {
    setRefreshing(true);
    try {
      const { data } = await linkedinApi.verifySession(ownerEmail);
      switch (data.status) {
        case 'ACTIVE':
          toast.success('Session is active.');
          if (data.account) setAccount(data.account);
          break;
        case 'REFRESHED':
          toast.success('Session refreshed successfully.');
          if (data.account) setAccount(data.account);
          else await fetchAccount();
          break;
        case 'PENDING_VERIFICATION':
          toast('LinkedIn needs a verification code to finish refreshing.', { icon: '🔐' });
          setVerification({ open: true, sessionId: data.session_id });
          break;
        case 'FAILED':
        default:
          toast.error(data.message || 'Session refresh failed.');
          await fetchAccount();
      }
    } catch (err) {
      toast.error(getErrorMessage(err, 'Session refresh failed.'));
    } finally {
      setRefreshing(false);
    }
  }

  /* ----------------------------- disconnect ----------------------------- */
  async function disconnect() {
    setDisconnecting(true);
    try {
      await linkedinApi.disconnect();
      toast.success('LinkedIn account disconnected.');
      setAccount(null);
      setNotConnected(true);
      setConfirmDisconnect(false);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not disconnect the account.'));
    } finally {
      setDisconnecting(false);
    }
  }

  function onVerificationResolved(status, data) {
    if (status === 'LOGIN_SUCCESS') {
      if (data?.account) setAccount(data.account);
      else fetchAccount();
      setNotConnected(false);
    }
  }

  /* -------------------------------- render ------------------------------ */
  if (loading) {
    return (
      <div className="max-w-3xl">
        <h1 className="text-2xl font-bold text-zinc-50">LinkedIn Account</h1>
        <div className="card mt-6 animate-pulse p-6">
          <div className="h-5 w-48 rounded bg-surface-700" />
          <div className="mt-4 h-4 w-72 rounded bg-surface-700" />
          <div className="mt-6 flex gap-3">
            <div className="h-9 w-32 rounded bg-surface-700" />
            <div className="h-9 w-24 rounded bg-surface-700" />
          </div>
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="max-w-3xl">
        <h1 className="text-2xl font-bold text-zinc-50">LinkedIn Account</h1>
        <div className="card mt-6 border-red-500/30 p-6 text-center">
          <p className="text-sm font-medium text-red-300">{loadError}</p>
          <p className="mt-1 text-xs text-zinc-500">
            The account could not be loaded — the backend may be unreachable.
          </p>
          <button
            onClick={() => {
              setLoading(true);
              fetchAccount();
            }}
            className="btn-secondary mt-4"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-zinc-50">LinkedIn Account</h1>
      <p className="mt-1 text-sm text-zinc-400">
        Connect the LinkedIn profile your campaigns will run from. Login happens in a real browser
        on our side, so it can take a moment.
      </p>

      {notConnected ? (
        /* --------------------------- connect form --------------------------- */
        <div className="card mt-6 p-6">
          <h2 className="text-lg font-semibold text-zinc-100">Connect your LinkedIn account</h2>
          <p className="mt-1 text-sm text-zinc-500">
            Your password is sent over HTTPS and stored only AES-256 encrypted. We never display it
            again.
          </p>

          <form onSubmit={connect} className="mt-5 space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="input-label" htmlFor="li-email">LinkedIn email</label>
                <input
                  id="li-email"
                  type="email"
                  className="input-field"
                  value={form.linkedin_email}
                  onChange={(e) => setForm((f) => ({ ...f, linkedin_email: e.target.value }))}
                  placeholder="you@gmail.com"
                  required
                  disabled={connecting}
                />
              </div>
              <div>
                <label className="input-label" htmlFor="li-label">
                  Label <span className="normal-case text-zinc-600">(optional)</span>
                </label>
                <input
                  id="li-label"
                  className="input-field"
                  value={form.label}
                  onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
                  placeholder="Work account"
                  maxLength={64}
                  disabled={connecting}
                />
              </div>
            </div>
            <div>
              <label className="input-label" htmlFor="li-password">LinkedIn password</label>
              <input
                id="li-password"
                type="password"
                className="input-field"
                value={form.linkedin_password}
                onChange={(e) => setForm((f) => ({ ...f, linkedin_password: e.target.value }))}
                required
                autoComplete="off"
                disabled={connecting}
              />
            </div>

            {connecting && (
              <SlowOperationNotice
                title="Connecting to LinkedIn… this can take up to 30 seconds."
                hint="We're logging in through a real browser session. If LinkedIn asks for a verification code, a code entry box will appear here."
                elapsedSeconds={elapsed}
              />
            )}

            {connectError && !connecting && (
              <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
                <p className="font-medium">Login failed</p>
                <p className="mt-0.5 text-red-300/90">{connectError}</p>
              </div>
            )}

            <button type="submit" className="btn-primary" disabled={connecting}>
              {connecting && <Spinner />}
              {connecting ? 'Connecting…' : 'Connect account'}
            </button>
          </form>
        </div>
      ) : (
        /* --------------------------- account card --------------------------- */
        <div className="card mt-6 p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent-500/10 text-xl font-bold text-accent-300">
                {(account?.linkedin_email || '?').slice(0, 1).toUpperCase()}
              </div>
              <div>
                <div className="flex flex-wrap items-center gap-2.5">
                  <h2 className="text-lg font-semibold text-zinc-100">{account.linkedin_email}</h2>
                  <AccountStatusBadge status={account.status} />
                </div>
                {account.label && <p className="mt-0.5 text-sm text-zinc-400">{account.label}</p>}
              </div>
            </div>
          </div>

          <dl className="mt-5 grid grid-cols-1 gap-4 border-t border-surface-700 pt-5 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs uppercase tracking-wide text-zinc-500">Owner</dt>
              <dd className="mt-0.5 text-zinc-300">{account.owner_email}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-zinc-500">Added</dt>
              <dd className="mt-0.5 text-zinc-300">{formatDate(account.created_at)}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-zinc-500">Last updated</dt>
              <dd className="mt-0.5 text-zinc-300">{formatDate(account.updated_at)}</dd>
            </div>
          </dl>

          {(account.status === 'failed' || account.status === 'suspended') && (
            <div className="mt-5 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
              {account.status === 'suspended'
                ? 'LinkedIn has suspended this account. Log in via LinkedIn directly to resolve it, then refresh the session.'
                : 'The last login attempt failed. Update the credentials and refresh the session to retry.'}
            </div>
          )}

          {refreshing && (
            <div className="mt-5">
              <SlowOperationNotice
                title="Checking LinkedIn session…"
                hint="Validating saved cookies and re-logging in if they expired — this can take up to 30 seconds."
                elapsedSeconds={refreshElapsed}
              />
            </div>
          )}

          <div className="mt-6 flex flex-wrap gap-3">
            <button onClick={refreshSession} className="btn-primary" disabled={refreshing}>
              {refreshing && <Spinner />}
              {refreshing ? 'Checking…' : 'Refresh session'}
            </button>
            <button onClick={() => setEditOpen(true)} className="btn-secondary" disabled={refreshing}>
              Edit
            </button>
            <button
              onClick={() => setConfirmDisconnect(true)}
              className="btn-danger"
              disabled={refreshing}
            >
              Disconnect
            </button>
          </div>
        </div>
      )}

      <VerificationCodeModal
        open={verification.open}
        sessionId={verification.sessionId}
        onClose={() => setVerification({ open: false, sessionId: null })}
        onResolved={onVerificationResolved}
      />

      <EditAccountModal
        open={editOpen}
        account={account}
        onClose={() => setEditOpen(false)}
        onSaved={setAccount}
      />

      {/* Disconnect confirmation */}
      <Modal
        open={confirmDisconnect}
        onClose={disconnecting ? undefined : () => setConfirmDisconnect(false)}
        title="Disconnect LinkedIn account?"
      >
        <p className="text-sm text-zinc-400">
          This removes the saved credentials and session for{' '}
          <span className="font-medium text-zinc-200">{account?.linkedin_email}</span>. Campaigns
          tied to this account will stop running. This cannot be undone.
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <button className="btn-secondary" onClick={() => setConfirmDisconnect(false)} disabled={disconnecting}>
            Keep account
          </button>
          <button className="btn-danger" onClick={disconnect} disabled={disconnecting}>
            {disconnecting && <Spinner />}
            {disconnecting ? 'Disconnecting…' : 'Disconnect'}
          </button>
        </div>
      </Modal>
    </div>
  );
}
