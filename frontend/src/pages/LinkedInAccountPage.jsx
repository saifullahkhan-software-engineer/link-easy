import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
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
      if (form.label.trim() !== (account.label || '')) payload.label = form.label.trim();
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
        // Don't block the user - allow them to still see the connect form
        setAccount(null);
        setNotConnected(true);
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
    if (!ownerEmail) {
      toast.error('Owner email missing — please log in again.');
      return;
    }
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
      setConnectError(getErrorMessage(err, 'LinkedIn login failed.'));
    } finally {
      setConnecting(false);
    }
  }

  /* --------------------------- refresh session -------------------------- */
  async function refreshSession() {
    if (!ownerEmail) {
      toast.error('Owner email missing.');
      return;
    }
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
        case 'IN_USE':
          toast.error(data.message || 'Account is busy.');
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
      toast.success('Verification succeeded — account is now active!');
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

  const hasAccount = Boolean(account);

  return (
    <div className="max-w-3xl">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-zinc-50">LinkedIn Account</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Connect the LinkedIn profile your campaigns will run from. Login happens in a real browser
            on our side, so it can take a moment.
          </p>
        </div>
        <Link to="/app/account" className="btn-secondary text-xs">
          ← Accounts
        </Link>
      </div>

      {loadError && (
        <div className="card mt-6 border-amber-500/30 bg-amber-500/5 p-4">
          <div className="flex gap-3">
            <span className="text-amber-400">⚠</span>
            <div className="flex-1">
              <p className="text-sm font-medium text-amber-200">{loadError}</p>
              <p className="mt-1 text-xs text-zinc-400">
                You can still try to connect your account below. If the problem persists, check if the
                backend is running.
              </p>
              <button
                onClick={() => {
                  setLoading(true);
                  fetchAccount();
                }}
                className="btn-secondary mt-3 text-xs"
              >
                Retry loading
              </button>
            </div>
          </div>
        </div>
      )}

      {!hasAccount ? (
        /* --------------------------- connect form --------------------------- */
        <div className="card mt-6 p-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-zinc-100">Connect your LinkedIn account</h2>
              <p className="mt-1 text-sm text-zinc-500">
                Your password is sent over HTTPS and stored only AES-256 encrypted. We never display it again.
              </p>
            </div>
            <div className="hidden sm:flex h-10 w-10 items-center justify-center rounded-xl bg-accent-500/10 text-accent-300">
              <svg className="h-6 w-6" viewBox="0 0 24 24" fill="currentColor">
                <path d="M19 0h-14c-2.761 0-5 2.239-5 5v14c0 2.761 2.239 5 5 5h14c2.762 0 5-2.239 5-5v-14c0-2.761-2.238-5-5-5zm-11 19h-3v-11h3v11zm-1.5-12.268c-.966 0-1.75-.79-1.75-1.764s.784-1.764 1.75-1.764 1.75.79 1.75 1.764-.783 1.764-1.75 1.764zm13.5 12.268h-3v-5.604c0-3.368-4-3.113-4 0v5.604h-3v-11h3v1.765c1.396-2.586 7-2.777 7 2.476v6.759z" />
              </svg>
            </div>
          </div>

          {!ownerEmail && (
            <div className="mt-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
              No owner email detected — please log out and log back in. Your account session may have expired.
            </div>
          )}

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
                title="Connecting to LinkedIn… this usually takes 10–20 seconds."
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

            <div className="flex flex-wrap items-center gap-3 pt-1">
              <button type="submit" className="btn-primary px-6" disabled={connecting}>
                {connecting && <Spinner />}
                {connecting ? 'Connecting…' : 'Connect LinkedIn account'}
              </button>
              <span className="text-xs text-zinc-500">Takes ~10-20s • Secure & encrypted</span>
            </div>

            <div className="rounded-lg bg-surface-800/60 p-3 text-xs leading-relaxed text-zinc-400">
              <p className="font-medium text-zinc-300">First time?</p>
              <ul className="mt-1 list-disc pl-4 space-y-0.5">
                <li>Use the email & password you normally use on linkedin.com</li>
                <li>If LinkedIn asks for a PIN, you'll get a popup to enter it</li>
                <li>You can disconnect anytime from this page</li>
              </ul>
            </div>
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
            <button onClick={fetchAccount} className="btn-secondary text-xs">
              ↻ Refresh
            </button>
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
            <div>
              <dt className="text-xs uppercase tracking-wide text-zinc-500">Status details</dt>
              <dd className="mt-0.5 text-zinc-300 capitalize">{account.status?.replace('_',' ')}</dd>
            </div>
          </dl>

          {(account.status === 'failed' || account.status === 'suspended') && (
            <div className="mt-5 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
              {account.status === 'suspended'
                ? 'LinkedIn has suspended this account. Log in via LinkedIn directly to resolve it, then refresh the session.'
                : 'The last login attempt failed. Update the credentials and refresh the session to retry.'}
              <div className="mt-2 flex gap-2">
                <button onClick={() => setEditOpen(true)} className="btn-secondary text-xs">
                  Update credentials
                </button>
                <button onClick={refreshSession} className="btn-primary text-xs">
                  Retry now
                </button>
              </div>
            </div>
          )}

          {account.status === 'pending_verification' && (
            <div className="mt-5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
              <p className="font-medium">Verification needed</p>
              <p className="mt-1 text-amber-200/80">LinkedIn requested an extra check. Refresh to trigger code entry, or check your email.</p>
              <button onClick={refreshSession} className="btn-primary mt-3 text-xs">
                Refresh & verify
              </button>
            </div>
          )}

          {refreshing && (
            <div className="mt-5">
              <SlowOperationNotice
                title="Checking LinkedIn session…"
                hint="Validating saved cookies and re-logging in if they expired — this usually takes 10–20 seconds."
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
            <Link to="/app/campaigns/create" className="btn-secondary">
              Create campaign →
            </Link>
          </div>
        </div>
      )}

      {/* Always-visible help card when no account, plus quick actions */}
      {!hasAccount && !loading && (
        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="card p-4">
            <h3 className="text-sm font-semibold text-zinc-200">How it works</h3>
            <ol className="mt-2 list-decimal space-y-1 pl-4 text-xs text-zinc-400">
              <li>Enter your LinkedIn email + password</li>
              <li>We log in in a secure cloud browser</li>
              <li>If 2FA appears, enter the code here</li>
              <li>Start creating campaigns immediately</li>
            </ol>
          </div>
          <div className="card p-4">
            <h3 className="text-sm font-semibold text-zinc-200">Need help?</h3>
            <p className="mt-2 text-xs text-zinc-400">
              If you get stuck at login, LinkedIn may have triggered a checkpoint.
              Try logging in manually on linkedin.com first, then return here.
            </p>
            <div className="mt-3 flex gap-2">
              <Link to="/" className="btn-secondary text-xs">Home</Link>
              <button onClick={fetchAccount} className="btn-secondary text-xs">Retry</button>
            </div>
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
