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
import LinkedInUnavailableNotice from '../components/LinkedInUnavailableNotice';
import { AddAccountCard, ConnectedAccountCard, LinkedInGlyph } from '../components/accounts/AccountCards';
import { useFeatures } from '../hooks/useFeatures';

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
      const { data } = await linkedinApi.updateAccount(payload, account?.id);
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
/**
 * Manage LinkedIn: one card per connected profile plus a "Connect another
 * profile" action. The Accounts hub only says how many profiles are connected.
 */
export default function LinkedInAccountPage() {
  const { email: ownerEmail } = useAuth();
  const { linkedinEnabled, linkedinMessage, loading: featuresLoading } = useFeatures();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [accounts, setAccounts] = useState([]);

  const [showConnect, setShowConnect] = useState(false);
  const [form, setForm] = useState({ linkedin_email: '', linkedin_password: '', label: '' });
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef(null);
  const connectRef = useRef(null);

  const [verification, setVerification] = useState({ open: false, sessionId: null });
  const [refreshingId, setRefreshingId] = useState(null);
  const [refreshElapsed, setRefreshElapsed] = useState(0);
  const [editAccount, setEditAccount] = useState(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(null);
  const [disconnecting, setDisconnecting] = useState(false);

  /** Every connected profile. Falls back to the single-account endpoint so
   *  older backends (and installs with one profile) still render a card. */
  const loadAccounts = useCallback(async () => {
    setLoadError(null);
    try {
      const { data } = await linkedinApi.listAccounts();
      const list = Array.isArray(data) ? data : [];
      if (list.length) {
        setAccounts(list);
        return;
      }
      throw new Error('empty');
    } catch (listErr) {
      try {
        const { data } = await linkedinApi.getAccount();
        setAccounts(data ? [data] : []);
      } catch (err) {
        setAccounts([]);
        if (err?.response?.status !== 404 && listErr?.response) {
          setLoadError(getErrorMessage(err, 'Could not load your LinkedIn profiles.'));
        }
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  const upsertAccount = useCallback((updated) => {
    if (!updated) return;
    setAccounts((prev) => {
      const exists = prev.some((a) => a.id === updated.id);
      return exists ? prev.map((a) => (a.id === updated.id ? updated : a)) : [...prev, updated];
    });
  }, []);

  // elapsed timers for slow operations
  useEffect(() => {
    if (connecting) {
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
      return () => clearInterval(timerRef.current);
    }
    return undefined;
  }, [connecting]);

  useEffect(() => {
    if (refreshingId) {
      setRefreshElapsed(0);
      const t = setInterval(() => setRefreshElapsed((s) => s + 1), 1000);
      return () => clearInterval(t);
    }
    return undefined;
  }, [refreshingId]);

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
        upsertAccount(data.account);
        setForm({ linkedin_email: '', linkedin_password: '', label: '' });
        setShowConnect(false);
        loadAccounts();
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

  const openConnectForm = () => {
    setShowConnect(true);
    setConnectError(null);
    setTimeout(() => connectRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0);
  };

  /* --------------------------- refresh session -------------------------- */
  async function refreshSession(account) {
    if (!ownerEmail) {
      toast.error('Owner email missing.');
      return;
    }
    setRefreshingId(account.id);
    try {
      const { data } = await linkedinApi.verifySession(account.id || null);
      if (data.profile_missing) {
        toast(
          'The stored browser profile was missing, so this check started from a blank browser. If this keeps happening, the /app/profiles volume is not mounted.',
          { icon: '⚠️', duration: 8000 },
        );
      }
      switch (data.status) {
        case 'ACTIVE':
          toast.success('Session is active.');
          upsertAccount(data.account);
          break;
        case 'REFRESHED':
          toast.success('Session refreshed successfully.');
          if (data.account) upsertAccount(data.account);
          else await loadAccounts();
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
          await loadAccounts();
      }
    } catch (err) {
      toast.error(getErrorMessage(err, 'Session refresh failed.'));
    } finally {
      setRefreshingId(null);
    }
  }

  /* ----------------------------- disconnect ----------------------------- */
  async function disconnect() {
    const target = confirmDisconnect;
    if (!target) return;
    setDisconnecting(true);
    try {
      await linkedinApi.disconnect(target.id || null);
      toast.success('LinkedIn account disconnected.');
      setAccounts((prev) => prev.filter((a) => a.id !== target.id));
      setConfirmDisconnect(null);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not disconnect the account.'));
    } finally {
      setDisconnecting(false);
    }
  }

  function onVerificationResolved(status, data) {
    if (status === 'LOGIN_SUCCESS') {
      if (data?.account) upsertAccount(data.account);
      loadAccounts();
      setShowConnect(false);
      toast.success('Verification succeeded — account is now active!');
    }
  }

  /* -------------------------------- render ------------------------------ */
  const backLink = (
    <Link to="/app/account" className="btn-secondary text-xs" data-testid="back-to-accounts">
      ← Accounts
    </Link>
  );

  if (loading) {
    return (
      <div className="mx-auto max-w-5xl">
        {backLink}
        <h1 className="mt-4 text-2xl font-bold text-zinc-50">LinkedIn profiles</h1>
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

  const hasAccounts = accounts.length > 0;
  const gated = !featuresLoading && !linkedinEnabled;

  /** One profile card — the design every manage page mirrors. */
  const renderCard = (account) => (
    <ConnectedAccountCard
      key={account.id || account.linkedin_email}
      testId={`linkedin-account-${account.id || account.linkedin_email}`}
      icon={(account.linkedin_email || '?').slice(0, 1).toUpperCase()}
      iconClass="bg-accent-500/10 text-accent-300"
      title={account.linkedin_email}
      subtitle={account.label || 'LinkedIn profile'}
      badge={<AccountStatusBadge status={account.status} />}
      details={[
        { label: 'Owner', value: account.owner_email || '—' },
        { label: 'Added', value: formatDate(account.created_at) },
        { label: 'Last updated', value: formatDate(account.updated_at) },
        { label: 'Status', value: (account.status || '—').replace('_', ' ') },
      ]}
      actions={
        gated ? (
          <button type="button" className="btn-danger text-xs" onClick={() => setConfirmDisconnect(account)}>
            Disconnect
          </button>
        ) : (
          <>
            <button
              type="button"
              className="btn-secondary text-xs"
              onClick={() => refreshSession(account)}
              disabled={refreshingId === account.id}
            >
              {refreshingId === account.id && <Spinner />}
              {refreshingId === account.id ? 'Checking…' : 'Refresh session'}
            </button>
            <button
              type="button"
              className="btn-secondary text-xs"
              onClick={() => setEditAccount(account)}
              disabled={refreshingId === account.id}
            >
              Edit
            </button>
            <button
              type="button"
              className="btn-danger text-xs"
              onClick={() => setConfirmDisconnect(account)}
              disabled={refreshingId === account.id}
            >
              Disconnect
            </button>
          </>
        )
      }
    >
      {(account.status === 'failed' || account.status === 'suspended') && (
        <p className="mt-4 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {account.status === 'suspended'
            ? 'LinkedIn has suspended this profile. Log in on linkedin.com to resolve it, then refresh the session.'
            : 'The last login attempt failed. Update the credentials and refresh the session to retry.'}
        </p>
      )}
      {account.status === 'pending_verification' && (
        <p className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          Verification needed — refresh the session to enter the code LinkedIn sent.
        </p>
      )}
      {refreshingId === account.id && (
        <div className="mt-4">
          <SlowOperationNotice
            title="Checking LinkedIn session…"
            hint="Validating saved cookies and re-logging in if they expired — this may take up to two minutes on a cold start."
            elapsedSeconds={refreshElapsed}
          />
        </div>
      )}
    </ConnectedAccountCard>
  );

  return (
    <div className="mx-auto max-w-5xl">
      {backLink}

      <header className="mt-4 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-accent-400">Main accounts</p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent-500/10 text-accent-300">
              <LinkedInGlyph />
            </span>
            <h1 className="text-2xl font-bold text-zinc-50">LinkedIn profiles</h1>
          </div>
          <p className="mt-2 text-sm text-zinc-400">
            {gated
              ? 'LinkedIn automation is paused on this deployment. Connected profiles stay linked.'
              : 'Every LinkedIn profile your campaigns, scans and live chat can run from. Login happens in a real browser on our side, so connecting can take up to two minutes on a cold start.'}
          </p>
        </div>
        {!gated && hasAccounts && !showConnect && (
          <button type="button" className="btn-primary" onClick={openConnectForm} data-testid="linkedin-add-account">
            Connect another profile
          </button>
        )}
      </header>

      {gated && <LinkedInUnavailableNotice message={linkedinMessage} className="mt-6" />}

      {loadError && (
        <div className="card mt-6 border-amber-500/30 bg-amber-500/5 p-4">
          <div className="flex gap-3">
            <span className="text-amber-400">⚠</span>
            <div className="flex-1">
              <p className="text-sm font-medium text-amber-200">{loadError}</p>
              <p className="mt-1 text-xs text-zinc-400">
                You can still try to connect a profile below. If the problem persists, check if the backend is running.
              </p>
              <button
                onClick={() => {
                  setLoading(true);
                  loadAccounts();
                }}
                className="btn-secondary mt-3 text-xs"
              >
                Retry loading
              </button>
            </div>
          </div>
        </div>
      )}

      <section className="mt-6" aria-label="Connected LinkedIn profiles">
        <h2 className="text-sm font-semibold text-zinc-300">
          {hasAccounts
            ? `${accounts.length} ${accounts.length === 1 ? 'profile' : 'profiles'} connected`
            : 'No profile connected yet'}
        </h2>

        <div className="mt-4 grid grid-cols-1 gap-5 lg:grid-cols-2">
          {accounts.map(renderCard)}
          {!gated && hasAccounts && !showConnect && (
            <AddAccountCard
              label="Connect another profile"
              hint="Add a second LinkedIn login to run campaigns from."
              onClick={openConnectForm}
              testId="linkedin-add-account-card"
            />
          )}
        </div>
      </section>

      {!gated && hasAccounts && (
        <>
          <div className="mt-6 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
            <p className="font-medium">⚠️ In early versions, running jobs need to be stopped before using Live Chat</p>
            <p className="mt-1 text-amber-200/80">
              Campaigns and live chat share the same LinkedIn session. Pause or stop your campaigns (or wait for them
              to finish) before opening Live Chat, otherwise jobs will pause automatically while chat is open.
            </p>
          </div>

          <div className="mt-5 flex flex-wrap gap-3">
            <Link to="/app/feed-scroll" className="btn-primary">LinkedIn Scan</Link>
            <Link to="/app/linkedin-live" className="btn-primary">Live Chat</Link>
            <Link to="/app/campaigns/create" className="btn-secondary">Create campaign →</Link>
          </div>
        </>
      )}

      {/* --------------------------- connect form --------------------------- */}
      {!gated && (!hasAccounts || showConnect) && (
        <div className="card mt-6 p-6" ref={connectRef}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold text-zinc-100">
                {hasAccounts ? 'Connect another LinkedIn profile' : 'Connect your LinkedIn account'}
              </h2>
              <p className="mt-1 text-sm text-zinc-500">
                Your password is sent over HTTPS and stored only AES-256 encrypted. We never display it again.
              </p>
            </div>
            {hasAccounts && (
              <button type="button" className="btn-secondary text-xs" onClick={() => setShowConnect(false)} disabled={connecting}>
                Cancel
              </button>
            )}
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
                title="Connecting to LinkedIn… this may take 30–40 seconds."
                hint="We're logging in through a real browser session — on the free beta this can be slower than usual, so please don't close the page. If LinkedIn asks for a verification code, a code entry box will appear here."
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
              <button type="submit" className="btn-primary px-6" disabled={connecting} data-testid="linkedin-connect">
                {connecting && <Spinner />}
                {connecting ? 'Connecting…' : 'Connect LinkedIn account'}
              </button>
              <span className="text-xs text-zinc-500">Takes ~30–40s • Secure &amp; encrypted</span>
            </div>

            <div className="rounded-lg bg-surface-800/60 p-3 text-xs leading-relaxed text-zinc-400">
              <p className="font-medium text-zinc-300">First time?</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                <li>Use the email &amp; password you normally use on linkedin.com</li>
                <li>If LinkedIn asks for a PIN, you&apos;ll get a popup to enter it</li>
                <li>You can disconnect any profile from its card above</li>
              </ul>
            </div>
          </form>
        </div>
      )}

      {/* Help cards while nothing is connected */}
      {!gated && !hasAccounts && (
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
              If you get stuck at login, LinkedIn may have triggered a checkpoint. Try logging in manually on
              linkedin.com first, then return here.
            </p>
            <div className="mt-3 flex gap-2">
              <Link to="/app/account" className="btn-secondary text-xs">Accounts</Link>
              <button onClick={loadAccounts} className="btn-secondary text-xs">Retry</button>
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
        open={Boolean(editAccount)}
        account={editAccount}
        onClose={() => setEditAccount(null)}
        onSaved={upsertAccount}
      />

      {/* Disconnect confirmation */}
      <Modal
        open={Boolean(confirmDisconnect)}
        onClose={disconnecting ? undefined : () => setConfirmDisconnect(null)}
        title="Disconnect LinkedIn account?"
      >
        <p className="text-sm text-zinc-400">
          This removes the saved credentials and session for{' '}
          <span className="font-medium text-zinc-200">{confirmDisconnect?.linkedin_email}</span>. Campaigns tied to
          this profile will stop running. This cannot be undone.
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <button className="btn-secondary" onClick={() => setConfirmDisconnect(null)} disabled={disconnecting}>
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
