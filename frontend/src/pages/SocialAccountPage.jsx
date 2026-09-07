import { useCallback, useEffect, useState } from 'react';
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { socialSchedulerApi, PLATFORMS } from '../api/socialScheduler';
import { getErrorMessage } from '../api/client';
import { useAuth } from '../context/AuthContext';
import Modal from '../components/Modal';
import { Spinner } from '../components/Spinner';
import { PlatformIcon, formatDateTime } from '../components/social/SocialBits';
import {
  AddAccountCard,
  ConnectedAccountCard,
  ConnectionStatusPill,
  connectionCountLabel,
} from '../components/accounts/AccountCards';

const REQUIREMENTS = {
  youtube: 'A Google account with a YouTube channel. Grants upload access only.',
  instagram:
    'An Instagram Business or Creator account linked to a Facebook Page. Approve publishing and messaging permissions when connecting.',
  tiktok: 'A TikTok account. Grants video upload and publish access.',
  facebook:
    'A Facebook Page you manage. Sign in with its administrator and approve the publishing and messaging permissions.',
};

const EXTRAS = {
  facebook: 'Publishing & Messenger Chat',
  instagram: 'Publishing & Instagram Chat',
};

const INBOX_LINK = {
  instagram: { to: '/app/inbox/instagram', label: 'Open Instagram Chat' },
  facebook: { to: '/app/inbox/messenger', label: 'Open Messenger Chat' },
};

// Per-platform names for the OAuth app credential pair (what each provider's
// developer console calls them). The secret is write-only: it is sent to the
// backend when saving but never returned by any API response.
const CREDENTIAL_FIELDS = {
  youtube: { identifier: 'client_id', identifierLabel: 'Client ID', secret: 'client_secret', secretLabel: 'Client Secret' },
  instagram: { identifier: 'app_id', identifierLabel: 'App ID', secret: 'app_secret', secretLabel: 'App Secret' },
  tiktok: { identifier: 'client_key', identifierLabel: 'Client Key', secret: 'client_secret', secretLabel: 'Client Secret' },
  facebook: { identifier: 'app_id', identifierLabel: 'App ID', secret: 'app_secret', secretLabel: 'App Secret' },
};

/**
 * Manage one social platform: every connected account as its own card (the
 * LinkedIn manage-card design), plus connecting another account, reconnecting
 * and disconnecting. The Accounts hub only summarises this page.
 */
export default function SocialAccountPage() {
  const { platform } = useParams();
  const meta = PLATFORMS.find((p) => p.id === platform);
  const label = meta?.label || platform;
  const { isAdmin } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [conn, setConn] = useState(null);
  const [credential, setCredential] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [pending, setPending] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(null);

  // "Set up / manage app credentials" modal state (operators only).
  const [credsOpen, setCredsOpen] = useState(false);
  const [credsBusy, setCredsBusy] = useState(false);
  const [idValue, setIdValue] = useState('');
  const [secretValue, setSecretValue] = useState('');

  // Saved Facebook Group destinations — a manual checklist, not a connection.
  const [groups, setGroups] = useState([]);
  const [groupForm, setGroupForm] = useState({ name: '', url: '' });
  const [groupBusy, setGroupBusy] = useState(false);
  const [removingGroup, setRemovingGroup] = useState(null);

  const load = useCallback(async () => {
    if (!meta) return;
    setLoadError(null);
    try {
      const { data } = await socialSchedulerApi.listPlatforms();
      const rows = Array.isArray(data) ? data : [];
      setConn(rows.find((row) => row.platform === platform) || { platform, connected: false, configured: false, accounts: [] });
      if (platform === 'facebook') {
        try {
          const saved = await socialSchedulerApi.listShareTargets('facebook');
          setGroups(Array.isArray(saved.data) ? saved.data : []);
        } catch {
          // The checklist is a convenience; the connect cards must still work.
        }
      }
      if (isAdmin) {
        try {
          const creds = await socialSchedulerApi.listPlatformCredentials();
          const rowsCreds = Array.isArray(creds.data) ? creds.data : [];
          setCredential(rowsCreds.find((row) => row.platform === platform) || null);
        } catch {
          // The page still works without the operator credential summary.
        }
      }
    } catch (err) {
      setLoadError(getErrorMessage(err, `Failed to load your ${label} accounts`));
    } finally {
      setLoading(false);
    }
  }, [isAdmin, label, meta, platform]);

  useEffect(() => {
    load();
  }, [load]);

  // OAuth round-trip result, when the backend returns straight to this page.
  useEffect(() => {
    if (!meta) return;
    const returned = searchParams.get('platform');
    if (!returned) return;
    const error = searchParams.get('error');
    if (searchParams.get('connected') === '1') toast.success(`${label} connected`);
    else if (error) toast.error(`${label}: ${error}`, { duration: 8000 });
    const remaining = new URLSearchParams(searchParams);
    ['platform', 'connected', 'error'].forEach((param) => remaining.delete(param));
    navigate({ pathname: `/app/account/social/${platform}`, search: remaining.toString() }, { replace: true });
  }, [searchParams, navigate, platform, label, meta]);

  const connect = async () => {
    setPending(true);
    try {
      const { data } = await socialSchedulerApi.getAuthUrl(platform);
      window.location.assign(data.auth_url);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not start sign-in'));
      setPending(false);
    }
  };

  const disconnect = async () => {
    const target = confirmDisconnect;
    if (!target) return;
    setPending(true);
    try {
      const { data } = await socialSchedulerApi.disconnectPlatform(platform, target.accountId);
      toast.success(data?.message || 'Disconnected');
      setConfirmDisconnect(null);
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to disconnect'));
    } finally {
      setPending(false);
    }
  };

  const openCredentials = async () => {
    setCredsOpen(true);
    setIdValue('');
    setSecretValue('');
    try {
      const { data } = await socialSchedulerApi.listPlatformCredentials();
      const row = (Array.isArray(data) ? data : []).find((c) => c.platform === platform) || null;
      setCredential(row);
      if (row?.source === 'database' && row.identifier) setIdValue(row.identifier);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load the current app credentials'));
    }
  };

  const saveCredentials = async () => {
    const fields = CREDENTIAL_FIELDS[platform];
    const identifier = idValue.trim();
    if (!identifier || !secretValue) {
      toast.error(`Both ${fields.identifierLabel} and ${fields.secretLabel} are required.`);
      return;
    }
    setCredsBusy(true);
    try {
      await socialSchedulerApi.savePlatformCredentials(platform, {
        [fields.identifier]: identifier,
        [fields.secret]: secretValue,
      });
      toast.success(`${label} app credentials saved`);
      setCredsOpen(false);
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not save app credentials'));
    } finally {
      setCredsBusy(false);
    }
  };

  const removeCredentials = async () => {
    setCredsBusy(true);
    try {
      const { data } = await socialSchedulerApi.deletePlatformCredentials(platform);
      toast.success(data?.message || `${label} app credentials removed`);
      setCredsOpen(false);
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not remove app credentials'));
    } finally {
      setCredsBusy(false);
    }
  };

  const addGroup = async (event) => {
    event.preventDefault();
    const name = groupForm.name.trim();
    const url = groupForm.url.trim();
    if (!name) return toast.error('Give the group a name');
    if (!/^https?:\/\//i.test(url)) return toast.error('Paste the group link, starting with https://');
    setGroupBusy(true);
    try {
      const { data } = await socialSchedulerApi.createShareTarget({ name, url });
      setGroups((prev) =>
        prev.some((target) => target.id === data.id)
          ? prev.map((target) => (target.id === data.id ? data : target))
          : [...prev, data],
      );
      setGroupForm({ name: '', url: '' });
      toast.success('Group saved');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not save that group'));
    } finally {
      setGroupBusy(false);
    }
    return undefined;
  };

  const removeGroup = async (target) => {
    setRemovingGroup(target.id);
    try {
      await socialSchedulerApi.deleteShareTarget(target.id);
      setGroups((prev) => prev.filter((item) => item.id !== target.id));
      toast.success('Group removed');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not remove that group'));
    } finally {
      setRemovingGroup(null);
    }
  };

  if (!meta) return <Navigate to="/app/account" replace />;

  const accounts = Array.isArray(conn?.accounts) && conn.accounts.length
    ? conn.accounts
    : conn?.connected
      ? [{ id: null, account_name: conn.account_name, account_id: conn.account_id, connected_at: conn.connected_at, updated_at: conn.updated_at, reconnect_required: conn.reconnect_required }]
      : [];
  const configured = Boolean(conn?.configured);
  const operatorManaged = credential?.source === 'database';
  const inbox = INBOX_LINK[platform];

  return (
    <div className="mx-auto max-w-5xl">
      <Link to="/app/account" className="btn-secondary text-xs" data-testid="back-to-accounts">
        ← Accounts
      </Link>

      <header className="mt-4 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-accent-400">Socials</p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent-500/15 text-accent-300">
              <PlatformIcon platform={platform} className="h-6 w-6" />
            </span>
            <h1 className="text-2xl font-bold text-zinc-50">{label} accounts</h1>
            {!loading && <ConnectionStatusPill state={accounts.length ? (conn?.reconnect_required ? 'attention' : 'connected') : 'none'} />}
          </div>
          <p className="mt-2 text-sm text-zinc-400">
            {EXTRAS[platform] ? `${EXTRAS[platform]}. ` : ''}
            Used by Social Scheduler and Ultimate Inbox. Tokens are stored encrypted.
          </p>
        </div>
        {configured && accounts.length > 0 && (
          <button type="button" className="btn-primary" onClick={connect} disabled={pending} data-testid={`connect-${platform}`}>
            {pending && <Spinner />}
            Connect another account
          </button>
        )}
      </header>

      {loading ? (
        <div className="flex h-40 items-center justify-center gap-2 text-sm text-zinc-400" role="status">
          <Spinner /> Loading {label} accounts…
        </div>
      ) : loadError ? (
        <div className="mt-6 rounded-xl border border-red-500/20 bg-red-500/5 p-5" role="alert">
          <p className="text-sm text-red-300">{loadError}</p>
          <button type="button" className="btn-secondary mt-3" onClick={() => { setLoading(true); load(); }}>
            Retry connections
          </button>
        </div>
      ) : (
        <section className="mt-6" aria-label={`${label} connected accounts`}>
          <h2 className="text-sm font-semibold text-zinc-300">
            {connectionCountLabel(accounts.length)}
          </h2>

          {!configured ? (
            <div className="card mt-4 p-6">
              <p className="text-sm text-zinc-300">
                {label} is not set up on this instance yet
                {isAdmin ? '' : ' — the operator has not added its API credentials.'}
              </p>
              <p className="mt-2 text-sm text-zinc-500">{REQUIREMENTS[platform]}</p>
              {isAdmin ? (
                <button type="button" className="btn-secondary mt-4" onClick={openCredentials}>
                  Set up app credentials
                </button>
              ) : (
                <button type="button" className="btn-secondary mt-4" disabled title="Not configured on this instance">
                  Connect {label}
                </button>
              )}
            </div>
          ) : (
            <div className="mt-4 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {accounts.map((account) => (
                <ConnectedAccountCard
                  key={account.id || account.account_id || platform}
                  testId={`social-account-${account.id || account.account_id || platform}`}
                  icon={<PlatformIcon platform={platform} className="h-6 w-6" />}
                  iconClass="bg-accent-500/15 text-accent-300"
                  title={account.account_name || account.account_id || `${label} account`}
                  subtitle={account.account_name && account.account_id ? account.account_id : label}
                  badge={<ConnectionStatusPill state={account.reconnect_required ? 'attention' : 'connected'} />}
                  details={[
                    { label: 'Connected', value: formatDateTime(account.connected_at) || '—' },
                    { label: 'Last updated', value: formatDateTime(account.updated_at) || '—' },
                  ]}
                  actions={
                    <>
                      {account.reconnect_required && (
                        <button type="button" className="btn-primary text-xs" onClick={connect} disabled={pending}>
                          {pending && <Spinner />}
                          Reconnect
                        </button>
                      )}
                      {inbox && !account.reconnect_required && (
                        <Link to={inbox.to} className="btn-secondary text-xs">{inbox.label}</Link>
                      )}
                      <button
                        type="button"
                        className="btn-danger text-xs"
                        onClick={() =>
                          setConfirmDisconnect({
                            accountId: account.id,
                            name: account.account_name || account.account_id || `this ${label} account`,
                          })
                        }
                        disabled={pending}
                        data-testid={`disconnect-account-${account.id || platform}`}
                      >
                        Disconnect
                      </button>
                    </>
                  }
                />
              ))}

              {accounts.length === 0 ? (
                <div className="card p-6 sm:col-span-2 lg:col-span-3">
                  <h3 className="text-base font-semibold text-zinc-100">Connect your first {label} account</h3>
                  <p className="mt-1 text-sm text-zinc-400">{REQUIREMENTS[platform]}</p>
                  <button
                    type="button"
                    className="btn-primary mt-5"
                    onClick={connect}
                    disabled={pending}
                    data-testid={`connect-${platform}`}
                  >
                    {pending && <Spinner />}
                    Connect {label}
                  </button>
                </div>
              ) : (
                <AddAccountCard
                  label="Connect another account"
                  hint={`Sign in to add a second ${label} account.`}
                  onClick={connect}
                  disabled={pending}
                  testId={`add-${platform}-account`}
                />
              )}
            </div>
          )}

          {isAdmin && operatorManaged && (
            <button type="button" className="btn-secondary mt-5 text-xs" onClick={openCredentials}>
              Manage app credentials
            </button>
          )}
        </section>
      )}

      {/* Saved Facebook Groups — a manual-share list, not a connection */}
      {platform === 'facebook' && !loading && !loadError && (
        <div className="card mt-8 p-6" data-testid="saved-groups">
          <h3 className="text-base font-semibold text-zinc-100">Facebook groups for manual sharing</h3>
          <p className="mt-1 text-xs text-zinc-500">
            Facebook removed its Groups API in April 2024, so no app — including this one — can post into a group for
            you. Save the groups you use and the upload page will offer them as a checklist once a Reel is published.
          </p>

          {groups.length > 0 && (
            <ul className="mt-4 divide-y divide-surface-700 rounded-lg border border-surface-700">
              {groups.map((target) => (
                <li key={target.id} className="flex items-center gap-3 px-3 py-2.5">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-zinc-100">{target.name}</span>
                    <span className="block truncate text-xs text-zinc-500">{target.url}</span>
                  </span>
                  <a href={target.url} target="_blank" rel="noreferrer" className="shrink-0 text-xs text-accent-400 underline-offset-2 hover:underline">
                    Open
                  </a>
                  <button
                    type="button"
                    onClick={() => removeGroup(target)}
                    disabled={removingGroup === target.id}
                    data-testid={`remove-group-${target.id}`}
                    className="shrink-0 rounded-md border border-surface-600 px-2.5 py-1 text-xs text-zinc-300 transition hover:border-red-500/50 hover:text-red-200 disabled:opacity-50"
                  >
                    {removingGroup === target.id ? 'Removing…' : 'Remove'}
                  </button>
                </li>
              ))}
            </ul>
          )}

          <form onSubmit={addGroup} className="mt-4 flex flex-wrap gap-2">
            <input
              value={groupForm.name}
              onChange={(event) => setGroupForm((g) => ({ ...g, name: event.target.value }))}
              placeholder="Group name"
              aria-label="Group name"
              maxLength={120}
              className="min-w-[10rem] flex-1 rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-accent-500 focus:outline-none"
            />
            <input
              value={groupForm.url}
              onChange={(event) => setGroupForm((g) => ({ ...g, url: event.target.value }))}
              placeholder="https://www.facebook.com/groups/…"
              aria-label="Group link"
              maxLength={500}
              className="min-w-[14rem] flex-[2] rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-accent-500 focus:outline-none"
            />
            <button type="submit" className="btn-secondary" disabled={groupBusy}>
              {groupBusy && <Spinner />}
              Save group
            </button>
          </form>
        </div>
      )}

      <div className="mt-8 rounded-xl border border-surface-700 bg-surface-800/60 p-5 text-sm text-zinc-400">
        <h3 className="text-sm font-semibold text-zinc-200">How publishing works</h3>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed">
          <li>Every minute the scheduler picks up posts whose time has come and publishes them to each selected account.</li>
          <li>Expired access tokens are refreshed automatically where the platform allows it; otherwise the account shows “Reconnect needed” here and that publish fails with a clear reason.</li>
          <li>Disconnecting removes the stored tokens immediately. Already scheduled posts to that account will fail, and its inbox will be unavailable, until you reconnect.</li>
          {isAdmin && (
            <li>
              App credentials saved here are stored in the database and override the server's environment values for
              this platform. Secrets are write-only and never shown again after saving.
            </li>
          )}
        </ul>
      </div>

      {/* Operator app-credentials modal */}
      <Modal open={credsOpen} onClose={() => !credsBusy && setCredsOpen(false)} title={`${label} app credentials`}>
        <p className="text-sm text-zinc-400">
          These are the OAuth app credentials for this <strong className="text-zinc-300">whole instance</strong> — the
          app users sign in to when they connect {label}. Saved values are stored in the database and override the
          server's environment settings.
        </p>
        {credential?.source === 'database' && (
          <p className="mt-2 rounded-lg border border-surface-700 bg-surface-800/60 p-3 text-xs text-zinc-400">
            Currently stored in the database
            {credential.identifier ? ` — ${CREDENTIAL_FIELDS[platform]?.identifierLabel || 'ID'}: ${credential.identifier}` : ''}.
            The secret is not shown again; enter it once more to replace it.
          </p>
        )}
        <form
          className="mt-5 space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            saveCredentials();
          }}
        >
          <div>
            <label htmlFor={`cred-id-${platform}`} className="input-label">
              {CREDENTIAL_FIELDS[platform]?.identifierLabel || 'Client ID'}
            </label>
            <input
              id={`cred-id-${platform}`}
              className="input-field"
              value={idValue}
              onChange={(event) => setIdValue(event.target.value)}
              autoComplete="off"
              required
            />
          </div>
          <div>
            <label htmlFor={`cred-secret-${platform}`} className="input-label">
              {CREDENTIAL_FIELDS[platform]?.secretLabel || 'Client Secret'}
            </label>
            <input
              id={`cred-secret-${platform}`}
              type="password"
              className="input-field"
              value={secretValue}
              onChange={(event) => setSecretValue(event.target.value)}
              placeholder={credential?.has_secret ? 'Enter again to replace the saved secret' : 'Required'}
              autoComplete="new-password"
              required
            />
          </div>
          <div className="flex justify-end gap-3 pt-2">
            {credential?.source === 'database' && (
              <button type="button" className="btn-danger" onClick={removeCredentials} disabled={credsBusy}>
                {credsBusy && <Spinner />}
                Remove saved credentials
              </button>
            )}
            <button type="button" className="btn-secondary" onClick={() => setCredsOpen(false)} disabled={credsBusy}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={credsBusy}>
              {credsBusy && <Spinner />}
              Save credentials
            </button>
          </div>
        </form>
      </Modal>

      <Modal
        open={Boolean(confirmDisconnect)}
        onClose={() => setConfirmDisconnect(null)}
        title={confirmDisconnect?.accountId ? `Disconnect ${confirmDisconnect.name}?` : `Disconnect ${label}?`}
      >
        <p className="text-sm text-zinc-300">
          The stored tokens are deleted right away. Scheduled posts and the inbox that use this account stop working
          until you connect it again.
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" className="btn-secondary" onClick={() => setConfirmDisconnect(null)} disabled={pending}>
            Keep connected
          </button>
          <button type="button" className="btn-danger" onClick={disconnect} disabled={pending}>
            {pending && <Spinner />}
            Disconnect
          </button>
        </div>
      </Modal>
    </div>
  );
}
