import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { socialSchedulerApi, PLATFORMS } from '../../api/socialScheduler';
import { getErrorMessage } from '../../api/client';
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
 */
export default function SocialSettingsPage() {
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(null); // platform id being connected/disconnected
  const [confirmDisconnect, setConfirmDisconnect] = useState(null);
  const [searchParams, setSearchParams] = useSearchParams();

  const load = useCallback(async () => {
    try {
      const { data } = await socialSchedulerApi.listPlatforms();
      setConnections(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load platform connections'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const connectedCount = connections.filter((c) => c.connected).length;

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
          const busy = pending === p.id;
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
                  <div>
                    <button className="btn-secondary w-full" disabled title="Not configured on this instance">
                      Connect
                    </button>
                    <p className="mt-2 text-xs text-zinc-500">
                      Not available on this instance — the operator has not set up {p.label} API credentials.
                    </p>
                  </div>
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
          <li>Instagram fetches the video from this server, so the API must be publicly reachable for Reels to work.</li>
          <li>Disconnecting removes the stored tokens immediately. Already scheduled posts to that platform will fail until you reconnect.</li>
        </ul>
      </div>

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
