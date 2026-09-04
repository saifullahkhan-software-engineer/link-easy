import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { socialSchedulerApi, PLATFORMS } from '../../api/socialScheduler';
import { getErrorMessage } from '../../api/client';
import { useAuth } from '../../context/AuthContext';
import Modal from '../../components/Modal';
import { Spinner } from '../../components/Spinner';
import { PlatformIcon, SocialPageHeader, formatDateTime } from '../../components/social/SocialBits';

const REQUIREMENTS = {
  youtube: 'A Google account with a YouTube channel. Grants upload access only.',
  instagram:
    'An Instagram Business or Creator account linked to a Facebook Page. Reels are published through the Meta Graph API.',
  tiktok: 'A TikTok account. Grants video upload and publish access.',
  facebook:
    'A Facebook Page you manage. Sign in with the Facebook account that administers the Page and approve every permission; the first Page you can post to is connected and used for video uploads.',
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
 * Settings: one card per platform with connect / reconnect / disconnect.
 * Connecting opens the platform's OAuth consent page; the backend callback
 * stores the tokens (encrypted) and sends the browser back here with
 * ?platform=…&connected=1 or ?error=… which we surface as a toast.
 *
 * Operators (admins) can also set the platform's OAuth *app* credentials from
 * an unconfigured card ("Set up app credentials") or manage a database-saved
 * pair ("Manage app credentials"). Regular users only ever see whether a
 * platform is configured and connectable.
 */
export default function SocialSettingsPage() {
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

  const [searchParams, setSearchParams] = useSearchParams();

  const load = useCallback(async () => {
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
      toast.error(getErrorMessage(err, 'Failed to load platform connections'));
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
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);

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
    const platform = confirmDisconnect;
    if (!platform) return;
    setPending(platform);
    try {
      const { data } = await socialSchedulerApi.disconnectPlatform(platform);
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

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const connectedCount = connections.filter((c) => c.connected).length;
  const credsPlatformLabel = PLATFORMS.find((p) => p.id === credsPlatform)?.label || credsPlatform || '';
  const activeCred = credentials.find((c) => c.platform === credsPlatform);

  return (
    <div className="mx-auto max-w-5xl">
      <SocialPageHeader
        current="/app/social-scheduler/settings"
        title="Settings"
        description={`Connected platforms: ${connectedCount} of ${PLATFORMS.length}. Tokens are stored encrypted and only used to publish your scheduled posts.`}
      />

      <div className="grid gap-5 md:grid-cols-3">
        {PLATFORMS.map((p) => {
          const conn = connections.find((c) => c.platform === p.id) || { platform: p.id, connected: false, configured: false };
          const cred = credentials.find((c) => c.platform === p.id);
          const busy = pending === p.id;
          const operatorManaged = Boolean(cred?.source === 'database');
          return (
            <div key={p.id} className="card flex flex-col p-6" data-testid={`platform-card-${p.id}`}>
              <div className="flex items-start justify-between gap-3">
                <PlatformIcon
                  platform={p.id}
                  className={`h-11 w-11 rounded-xl ${conn.connected ? 'bg-accent-500/15 text-accent-300' : 'bg-surface-700 text-zinc-400'}`}
                />
                <ConnectionBadge conn={conn} />
              </div>
              <h2 className="mt-4 text-base font-semibold text-zinc-100">{p.label}</h2>
              {conn.connected ? (
                <div className="mt-1 space-y-0.5 text-sm">
                  <p className="truncate text-zinc-300">{conn.account_name || conn.account_id || 'Connected account'}</p>
                  <p className="text-xs text-zinc-500">
                    Connected {formatDateTime(conn.connected_at)}
                    {conn.expires_at && (
                      <>
                        {' · '}
                        {new Date(conn.expires_at) < new Date() ? 'expired' : 'token valid until'}{' '}
                        {formatDateTime(conn.expires_at)}
                      </>
                    )}
                  </p>
                  {conn.reconnect_required && (
                    <p className="text-xs text-amber-300">
                      The access token has expired and cannot be renewed automatically. Reconnect before your next
                      scheduled post.
                    </p>
                  )}
                </div>
              ) : (
                <p className="mt-1 text-sm text-zinc-400">{REQUIREMENTS[p.id]}</p>
              )}

              <div className="mt-auto pt-5">
                {!conn.configured ? (
                  isAdmin ? (
                    <div>
                      <button className="btn-secondary w-full" onClick={() => openCredentialsModal(p.id)}>
                        Set up app credentials
                      </button>
                      <p className="mt-2 text-xs text-zinc-500">
                        {p.label} is not configured yet. Add this instance's OAuth app credentials to enable Connect.
                      </p>
                    </div>
                  ) : (
                    <div>
                      <button className="btn-secondary w-full" disabled title="Not configured on this instance">
                        Connect
                      </button>
                      <p className="mt-2 text-xs text-zinc-500">
                        Not available on this instance — the operator has not set up {p.label} API credentials.
                      </p>
                    </div>
                  )
                ) : conn.connected ? (
                  <div className="flex gap-2">
                    <button className="btn-secondary flex-1" onClick={() => connect(p.id)} disabled={Boolean(pending)}>
                      {busy && <Spinner />}
                      Reconnect
                    </button>
                    <button
                      className="btn-danger flex-1"
                      onClick={() => setConfirmDisconnect(p.id)}
                      disabled={Boolean(pending)}
                    >
                      Disconnect
                    </button>
                  </div>
                ) : (
                  <button className="btn-primary w-full" onClick={() => connect(p.id)} disabled={Boolean(pending)}>
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
      </div>

      <div className="mt-8 rounded-xl border border-surface-700 bg-surface-800/60 p-5 text-sm text-zinc-400">
        <h3 className="text-sm font-semibold text-zinc-200">How publishing works</h3>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed">
          <li>Every minute the scheduler picks up posts whose time has come and publishes them to each selected platform.</li>
          <li>Expired access tokens are refreshed automatically where the platform allows it; otherwise the platform shows “Reconnect needed” here and that publish fails with a clear reason.</li>
          <li>Instagram receives the video as a direct upload from this server’s upload folder, so no public URL or tunnel (ngrok) is needed for Reels.</li>
          <li>Disconnecting removes the stored tokens immediately. Already scheduled posts to that platform will fail until you reconnect.</li>
          {isAdmin && (
            <li>
              App credentials saved here are stored in the database and override the server's environment values for
              this platform. Secrets are write-only and never shown again after saving.
            </li>
          )}
        </ul>
      </div>

      {/* Saved Facebook Groups — a manual-share list, not a connection */}
      <div className="card mt-5 p-6" data-testid="saved-groups">
        <h2 className="text-base font-semibold text-zinc-100">Facebook groups for manual sharing</h2>
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
      </div>

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
        title={`Disconnect ${PLATFORMS.find((p) => p.id === confirmDisconnect)?.label || ''}?`}
      >
        <p className="text-sm text-zinc-300">
          The stored tokens are deleted right away. Posts already scheduled for this platform will fail until you
          connect it again.
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <button className="btn-secondary" onClick={() => setConfirmDisconnect(null)} disabled={Boolean(pending)}>
            Keep connected
          </button>
          <button className="btn-danger" onClick={disconnect} disabled={Boolean(pending)}>
            {pending && <Spinner />}
            Disconnect
          </button>
        </div>
      </Modal>
    </div>
  );
}
