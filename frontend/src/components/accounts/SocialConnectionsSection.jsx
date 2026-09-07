import { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { socialSchedulerApi, PLATFORMS } from '../../api/socialScheduler';
import { getErrorMessage } from '../../api/client';
import { useAuth } from '../../context/AuthContext';
import Modal from '../Modal';
import { Spinner } from '../Spinner';
import { PlatformIcon, formatDateTime } from '../social/SocialBits';
import { ChannelIcon } from '../inbox/InboxBits';

const REQUIREMENTS = {
  youtube: 'A Google account with a YouTube channel. Grants upload access only.',
  instagram:
    'An Instagram Business or Creator account linked to a Facebook Page. Approve publishing and messaging permissions when connecting.',
  tiktok: 'A TikTok account. Grants video upload and publish access.',
  facebook:
    'A Facebook Page you manage. Sign in with its administrator and approve the publishing and messaging permissions.',
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

function ConnectionBadge({ conn }) {
  if (!conn?.connected) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 px-2.5 py-1 text-xs font-medium text-zinc-300 ring-1 ring-inset ring-zinc-500/20">
        <span className="h-1.5 w-1.5 rounded-full bg-zinc-400" />
        Not connected
      </span>
    );
  }
  if (conn.reconnect_required) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-300 ring-1 ring-inset ring-amber-500/30">
        <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
        Reconnect needed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
      Connected
    </span>
  );
}

/**
 * Accounts → Socials: one card per platform with connect / reconnect / disconnect.
 * Connecting opens the platform's OAuth consent page; the backend callback
 * stores the tokens (encrypted) and sends the browser back here with
 * ?platform=…&connected=1 or ?error=… which we surface as a toast.
 *
 * Operators (admins) can also set the platform's OAuth *app* credentials from
 * an unconfigured card ("Set up app credentials") or manage a database-saved
 * pair ("Manage app credentials"). Regular users only ever see whether a
 * platform is configured and connectable.
 */
export default function SocialConnectionsSection() {
  const { isAdmin } = useAuth();
  const [connections, setConnections] = useState([]);
  const [credentials, setCredentials] = useState([]);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(null); // platform id being connected/disconnected
  const [confirmDisconnect, setConfirmDisconnect] = useState(null);

  // "Set up / manage app credentials" modal state.
  const [credsPlatform, setCredsPlatform] = useState(null);
  const [credsLoading, setCredsLoading] = useState(false);
  const [credsBusy, setCredsBusy] = useState(false);
  const [idValue, setIdValue] = useState('');
  const [secretValue, setSecretValue] = useState('');

  // Saved Facebook Group destinations. Meta closed the Groups API, so these
  // are only ever a manual checklist — this card exists so a mistyped group can
  // be renamed or removed (the upload page can add, not delete).
  const [groups, setGroups] = useState([]);
  const [groupForm, setGroupForm] = useState({ name: '', url: '' });
  const [groupBusy, setGroupBusy] = useState(false);
  const [removingGroup, setRemovingGroup] = useState(null);

  const [searchParams] = useSearchParams();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [loadError, setLoadError] = useState(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const { data } = await socialSchedulerApi.listPlatforms();
      setConnections(Array.isArray(data) ? data : []);
      try {
        const saved = await socialSchedulerApi.listShareTargets('facebook');
        setGroups(Array.isArray(saved.data) ? saved.data : []);
      } catch {
        // The checklist is a convenience; the connect cards must still work.
      }
      if (isAdmin) {
        try {
          const creds = await socialSchedulerApi.listPlatformCredentials();
          setCredentials(Array.isArray(creds.data) ? creds.data : []);
        } catch (err) {
          // The cards still work without the operator credential summary.
          toast.error(getErrorMessage(err, 'Failed to load app credentials'));
        }
      }
    } catch (err) {
      setLoadError(getErrorMessage(err, 'Failed to load platform connections'));
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    load();
  }, [load]);

  const addGroup = async (event) => {
    event.preventDefault();
    const name = groupForm.name.trim();
    const url = groupForm.url.trim();
    if (!name) return toast.error('Give the group a name');
    if (!/^https?:\/\//i.test(url)) return toast.error('Paste the group link, starting with https://');
    setGroupBusy(true);
    try {
      const { data } = await socialSchedulerApi.createShareTarget({ name, url });
      // Saving an existing URL returns that row rather than a duplicate.
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

  // OAuth round-trip result (backend redirects here).
  useEffect(() => {
    const platform = searchParams.get('platform');
    if (!platform) return;
    const label = PLATFORMS.find((p) => p.id === platform)?.label || platform;
    const error = searchParams.get('error');
    if (searchParams.get('connected') === '1') {
      toast.success(`${label} connected`);
    } else if (error) {
      toast.error(`${label}: ${error}`, { duration: 8000 });
    }
    // Clear the params so a refresh doesn't repeat the toast.
    const remaining = new URLSearchParams(searchParams);
    ['platform', 'connected', 'error'].forEach((key) => remaining.delete(key));
    navigate({ pathname, search: remaining.toString(), hash: '#socials' }, { replace: true });
  }, [searchParams, pathname, navigate]);

  const connect = async (platform) => {
    setPending(platform);
    try {
      const { data } = await socialSchedulerApi.getAuthUrl(platform);
      window.location.assign(data.auth_url);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not start sign-in'));
      setPending(null);
    }
  };

  const disconnect = async () => {
    const target = confirmDisconnect;
    if (!target?.platform) return;
    const { platform, accountId } = target;
    setPending(platform);
    try {
      const { data } = await socialSchedulerApi.disconnectPlatform(platform, accountId);
      toast.success(data?.message || 'Disconnected');
      setConfirmDisconnect(null);
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to disconnect'));
    } finally {
      setPending(null);
    }
  };

  const openCredentialsModal = async (platform) => {
    setCredsPlatform(platform);
    setIdValue('');
    setSecretValue('');
    setCredsLoading(true);
    try {
      const { data } = await socialSchedulerApi.listPlatformCredentials();
      const rows = Array.isArray(data) ? data : [];
      setCredentials(rows);
      const row = rows.find((c) => c.platform === platform);
      if (row?.source === 'database' && row.identifier) {
        setIdValue(row.identifier);
      }
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load the current app credentials'));
    } finally {
      setCredsLoading(false);
    }
  };

  const saveCredentials = async () => {
    const platform = credsPlatform;
    if (!platform) return;
    const fields = CREDENTIAL_FIELDS[platform];
    const identifier = idValue.trim();
    const secret = secretValue;
    if (!identifier || !secret) {
      toast.error(`Both ${fields.identifierLabel} and ${fields.secretLabel} are required.`);
      return;
    }
    const label = PLATFORMS.find((p) => p.id === platform)?.label || platform;
    setCredsBusy(true);
    try {
      await socialSchedulerApi.savePlatformCredentials(platform, {
        [fields.identifier]: identifier,
        [fields.secret]: secret,
      });
      toast.success(`${label} app credentials saved`);
      setCredsPlatform(null);
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not save app credentials'));
    } finally {
      setCredsBusy(false);
    }
  };

  const removeCredentials = async () => {
    const platform = credsPlatform;
    if (!platform) return;
    const label = PLATFORMS.find((p) => p.id === platform)?.label || platform;
    setCredsBusy(true);
    try {
      const { data } = await socialSchedulerApi.deletePlatformCredentials(platform);
      toast.success(data?.message || `${label} app credentials removed`);
      setCredsPlatform(null);
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not remove app credentials'));
    } finally {
      setCredsBusy(false);
    }
  };

  const connectedCount = connections.filter((c) => c.connected).length;
  const credsPlatformLabel = PLATFORMS.find((p) => p.id === credsPlatform)?.label || credsPlatform || '';
  const activeCred = credentials.find((c) => c.platform === credsPlatform);

  return (
    <section id="socials" aria-labelledby="socials-title" className="scroll-mt-20 lg:scroll-mt-6 border-t border-surface-700 pt-8">
      <div className="mb-5">
        <h2 id="socials-title" className="text-xl font-semibold text-zinc-100">Socials</h2>
        <p className="mt-1 text-sm text-zinc-400">
          Connect and manage the social accounts used by Social Scheduler and Ultimate Inbox.
        </p>
        {!loading && !loadError && (
          <p className="mt-2 text-xs text-zinc-500">Connected platforms: {connectedCount} of {PLATFORMS.length}. Tokens are stored encrypted.</p>
        )}
      </div>

      {loading ? (
        <div className="flex h-40 items-center justify-center gap-2 text-sm text-zinc-400" role="status">
          <Spinner /> Loading social connections…
        </div>
      ) : loadError ? (
        <div className="mb-5 rounded-xl border border-red-500/20 bg-red-500/5 p-5" role="alert">
          <p className="text-sm text-red-300">{loadError}</p>
          <button type="button" className="btn-secondary mt-3" onClick={() => { setLoading(true); load(); }}>Retry connections</button>
        </div>
      ) : null}
      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
        {!loading && !loadError && PLATFORMS.map((p) => {
          const conn = connections.find((c) => c.platform === p.id) || { platform: p.id, connected: false, configured: false };
          const cred = credentials.find((c) => c.platform === p.id);
          const busy = pending === p.id;
          const operatorManaged = Boolean(cred?.source === 'database');
          return (
            <div key={p.id} className="card relative flex h-full min-w-0 min-h-[258px] flex-col p-5" data-testid={`platform-card-${p.id}`}>
              <div className="flex items-start gap-3 pr-28">
                <PlatformIcon
                  platform={p.id}
                  className={`h-11 w-11 rounded-xl ${conn.connected ? 'bg-accent-500/15 text-accent-300' : 'bg-surface-700 text-zinc-400'}`}
                />
                <div className="absolute right-5 top-5"><ConnectionBadge conn={conn} /></div>
              </div>
              <h3 className="mt-4 text-base font-semibold text-zinc-100">{p.label}</h3>
              {p.id === 'facebook' && <p className="mt-1 text-xs text-zinc-500">Publishing & Messenger Chat</p>}
              {p.id === 'instagram' && <p className="mt-1 text-xs text-zinc-500">Publishing & Instagram Chat</p>}
              {conn.connected ? (
                <div className="mt-1 space-y-2">
                  <p className="text-xs font-medium text-zinc-400">
                    {conn.accounts.length} {conn.accounts.length === 1 ? 'account' : 'accounts'} connected
                  </p>
                  <ul className="max-h-40 space-y-1.5 overflow-y-auto pr-1">
                    {conn.accounts.map((acc) => (
                      <li
                        key={acc.id}
                        className="flex items-center justify-between gap-2 rounded-lg border border-surface-700 bg-surface-800/60 px-3 py-2"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm text-zinc-100">
                            {acc.account_name || acc.account_id || 'Account'}
                          </p>
                          {acc.reconnect_required ? (
                            <p className="text-[11px] text-amber-300">Reconnect needed</p>
                          ) : (
                            <p className="text-[11px] text-zinc-500">
                              Connected {formatDateTime(acc.connected_at)}
                            </p>
                          )}
                        </div>
                        <button
                          type="button"
                          onClick={() =>
                            setConfirmDisconnect({
                              platform: p.id,
                              accountId: acc.id,
                              name: acc.account_name || acc.account_id || 'this account',
                            })
                          }
                          disabled={Boolean(pending)}
                          data-testid={`disconnect-account-${acc.id}`}
                          className="shrink-0 rounded-md border border-surface-600 px-2 py-1 text-[11px] text-zinc-300 transition hover:border-red-500/50 hover:text-red-200 disabled:opacity-50"
                        >
                          Remove
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p className="mt-1 text-sm text-zinc-400">{REQUIREMENTS[p.id]}</p>
              )}

              <div className="mt-auto flex min-h-[76px] flex-col justify-end pt-5">
                {!conn.configured ? (
                  isAdmin ? (
                    <div>
                      <button className="btn-secondary min-h-11 w-full" onClick={() => openCredentialsModal(p.id)}>
                        Set up app credentials
                      </button>
                      <p className="mt-2 text-xs text-zinc-500">
                        {p.label} is not configured yet. Add this instance's OAuth app credentials to enable Connect.
                      </p>
                    </div>
                  ) : (
                    <div>
                      <button className="btn-secondary min-h-11 w-full" disabled title="Not configured on this instance">
                        Connect
                      </button>
                      <p className="mt-2 text-xs text-zinc-500">
                        Not available on this instance — the operator has not set up {p.label} API credentials.
                      </p>
                    </div>
                  )
                ) : conn.connected ? (
                  <div className="flex items-stretch gap-2">
                    <button className="btn-secondary min-h-11 flex-1" onClick={() => connect(p.id)} disabled={Boolean(pending)}>
                      {busy && <Spinner />}
                      Connect another account
                    </button>
                    <button
                      className="btn-danger min-h-11 flex-1"
                      onClick={() => setConfirmDisconnect({ platform: p.id, accountId: null, name: p.label })}
                      disabled={Boolean(pending)}
                      data-testid={`disconnect-platform-${p.id}`}
                    >
                      {conn.accounts.length > 1 ? 'Disconnect all' : 'Disconnect'}
                    </button>
                  </div>
                ) : (
                  <button className="btn-primary min-h-11 w-full" onClick={() => connect(p.id)} disabled={Boolean(pending)}>
                    {busy && <Spinner />}
                    Connect {p.label.split(' ')[0]}
                  </button>
                )}
                {isAdmin && operatorManaged && (
                  <button
                    className="mt-3 w-full border-t border-surface-700/70 pt-3 text-center text-xs font-medium text-zinc-400 transition hover:text-accent-300"
                    onClick={() => openCredentialsModal(p.id)}
                  >
                    Manage app credentials
                  </button>
                )}
              </div>
            </div>
          );
        })}
        <div className="card relative flex min-h-[258px] flex-col border-dashed p-5" data-testid="platform-card-whatsapp-business">
          <div className="flex items-start gap-3 pr-28">
            <ChannelIcon channel="whatsapp-business" className="h-11 w-11 rounded-xl bg-green-500/10 text-green-300" />
            <span className="absolute right-5 top-5 rounded-full bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-300 ring-1 ring-inset ring-amber-500/20">Coming soon</span>
          </div>
          <h3 className="mt-4 text-base font-semibold text-zinc-100">WhatsApp Business</h3>
          <p className="mt-1 text-sm text-zinc-400">Coming in a future update. Business connections and messaging are not available yet.</p>
          <div className="mt-auto pt-5">
            <button type="button" disabled className="btn-secondary min-h-11 w-full">Coming soon</button>
          </div>
        </div>
      </div>

      <div className="mt-8 rounded-xl border border-surface-700 bg-surface-800/60 p-5 text-sm text-zinc-400">
        <h3 className="text-sm font-semibold text-zinc-200">How publishing works</h3>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed">
          <li>Every minute the scheduler picks up posts whose time has come and publishes them to each selected platform.</li>
          <li>Expired access tokens are refreshed automatically where the platform allows it; otherwise the platform shows “Reconnect needed” here and that publish fails with a clear reason.</li>
          <li>Instagram receives the video as a direct upload from this server’s upload folder, so no public URL or tunnel (ngrok) is needed for Reels.</li>
          <li>Disconnecting removes the stored tokens immediately. Already scheduled posts to that platform will fail, and its inbox will be unavailable, until you reconnect.</li>
          {isAdmin && (
            <li>
              App credentials saved here are stored in the database and override the server's environment values for
              this platform. Secrets are write-only and never shown again after saving.
            </li>
          )}
        </ul>
      </div>

      {/* Saved Facebook Groups — a manual-share list, not a connection */}
      {!loading && !loadError && <div className="card mt-5 p-6" data-testid="saved-groups">
        <h3 className="text-base font-semibold text-zinc-100">Facebook groups for manual sharing</h3>
        <p className="mt-1 text-xs text-zinc-500">
          Facebook removed its Groups API in April 2024, so no app — including this one — can post into a group for
          you. Save the groups you use and the upload page will offer them as a checklist once a Reel is published.
          Nothing here is ever posted automatically, and no Facebook login is needed.
        </p>

        {groups.length > 0 && (
          <ul className="mt-4 divide-y divide-surface-700 rounded-lg border border-surface-700">
            {groups.map((target) => (
              <li key={target.id} className="flex items-center gap-3 px-3 py-2.5">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-zinc-100">{target.name}</span>
                  <span className="block truncate text-xs text-zinc-500">{target.url}</span>
                </span>
                <a
                  href={target.url}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 text-xs text-accent-400 underline-offset-2 hover:underline"
                >
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
      </div>}

      {/* Operator app-credentials modal */}
      <Modal
        open={Boolean(credsPlatform)}
        onClose={() => !credsBusy && setCredsPlatform(null)}
        title={`${credsPlatformLabel} app credentials`}
      >
        {credsLoading ? (
          <div className="flex h-32 items-center justify-center">
            <Spinner />
          </div>
        ) : (
          <>
            <p className="text-sm text-zinc-400">
              These are the OAuth app credentials for this <strong className="text-zinc-300">whole instance</strong> —
              the app users sign in to when they connect {credsPlatformLabel}. Saved values are stored in the database
              and override the server's environment settings.
            </p>
            {activeCred?.source === 'database' && (
              <p className="mt-2 rounded-lg border border-surface-700 bg-surface-800/60 p-3 text-xs text-zinc-400">
                Currently stored in the database{activeCred.identifier ? ` — ${CREDENTIAL_FIELDS[credsPlatform]?.identifierLabel || 'ID'}: ${activeCred.identifier}` : ''}.
                The secret is not shown again; enter it once more to replace it.
              </p>
            )}
            <form
              className="mt-5 space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                saveCredentials();
              }}
            >
              <div>
                <label htmlFor={`cred-id-${credsPlatform}`} className="input-label">
                  {CREDENTIAL_FIELDS[credsPlatform]?.identifierLabel || 'Client ID'}
                </label>
                <input
                  id={`cred-id-${credsPlatform}`}
                  className="input-field"
                  value={idValue}
                  onChange={(e) => setIdValue(e.target.value)}
                  placeholder={CREDENTIAL_FIELDS[credsPlatform]?.identifierLabel || 'ID'}
                  autoComplete="off"
                  required
                />
              </div>
              <div>
                <label htmlFor={`cred-secret-${credsPlatform}`} className="input-label">
                  {CREDENTIAL_FIELDS[credsPlatform]?.secretLabel || 'Client Secret'}
                </label>
                <input
                  id={`cred-secret-${credsPlatform}`}
                  type="password"
                  className="input-field"
                  value={secretValue}
                  onChange={(e) => setSecretValue(e.target.value)}
                  placeholder={
                    activeCred?.has_secret ? 'Enter again to replace the saved secret' : 'Required'
                  }
                  autoComplete="new-password"
                  required
                />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                {activeCred?.source === 'database' && (
                  <button
                    type="button"
                    className="btn-danger"
                    onClick={removeCredentials}
                    disabled={credsBusy}
                  >
                    {credsBusy && <Spinner />}
                    Remove saved credentials
                  </button>
                )}
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setCredsPlatform(null)}
                  disabled={credsBusy}
                >
                  Cancel
                </button>
                <button type="submit" className="btn-primary" disabled={credsBusy}>
                  {credsBusy && <Spinner />}
                  Save credentials
                </button>
              </div>
            </form>
          </>
        )}
      </Modal>

      <Modal
        open={Boolean(confirmDisconnect)}
        onClose={() => setConfirmDisconnect(null)}
        title={
          confirmDisconnect?.accountId
            ? `Remove ${confirmDisconnect.name}?`
            : `Disconnect ${PLATFORMS.find((p) => p.id === confirmDisconnect?.platform)?.label || ''}?`
        }
      >
        <p className="text-sm text-zinc-300">
          {confirmDisconnect?.accountId ? (
            <>
              This account&apos;s stored tokens are deleted right away. Scheduled posts and the inbox that use it stop
              working until you reconnect it. Your other {PLATFORMS.find((p) => p.id === confirmDisconnect?.platform)?.label || 'account'} connections are
              untouched.
            </>
          ) : (
            <>
              The stored tokens are deleted right away. Posts already scheduled for this platform will fail until you
              connect it again. Its inbox will also be unavailable until you reconnect.
            </>
          )}
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <button className="btn-secondary" onClick={() => setConfirmDisconnect(null)} disabled={Boolean(pending)}>
            Keep connected
          </button>
          <button className="btn-danger" onClick={disconnect} disabled={Boolean(pending)}>
            {pending && <Spinner />}
            {confirmDisconnect?.accountId ? 'Remove account' : 'Disconnect'}
          </button>
        </div>
      </Modal>
    </section>
  );
}
