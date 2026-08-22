import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { whatsappApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';
import WhatsAppStatusBadge from '../components/whatsapp/WhatsAppStatusBadge';
import BrowserViewPanel from '../components/live/BrowserViewPanel';

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

/**
 * WhatsApp connectivity page (linked from the Accounts hub).
 *
 * Disconnected / waiting for QR → status card + embedded QR browser.
 * Connected → the same manage-account card design as LinkedIn: status badge,
 * "Added" / "Last updated", plus the two product shortcuts (WhatsApp Scan and
 * Live Chat) with the early-version note that running jobs must be stopped
 * before live chat can be used.
 */
export default function WhatsAppConnectPage() {
  const [status, setStatus] = useState('disconnected');
  const [sessionMeta, setSessionMeta] = useState({ created_at: null, updated_at: null, is_active: false });
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [showBrowserView, setShowBrowserView] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [captureFailed, setCaptureFailed] = useState(false);
  const connectionStartedRef = useRef(false);
  const hideBrowserTimerRef = useRef(null);
  const prevStatus = useRef(null);

  useEffect(() => () => {
    if (hideBrowserTimerRef.current) clearTimeout(hideBrowserTimerRef.current);
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const { data } = await whatsappApi.getStatus();
      const nextStatus = data.status || 'disconnected';
      setStatus(nextStatus);
      setSessionMeta({
        created_at: data.created_at || null,
        updated_at: data.updated_at || null,
        is_active: Boolean(data.is_active),
      });
      if (nextStatus === 'waiting_qr') {
        setShowBrowserView(true);
      } else if (nextStatus === 'connected' && connectionStartedRef.current) {
        setShowBrowserView(true);
        connectionStartedRef.current = false;
        if (hideBrowserTimerRef.current) clearTimeout(hideBrowserTimerRef.current);
        hideBrowserTimerRef.current = setTimeout(() => setShowBrowserView(false), 12000);
      } else if (nextStatus === 'disconnected' || nextStatus === 'error') {
        setShowBrowserView(false);
      }
    } catch {
      // Silently ignore — backend may be briefly unreachable.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  // Poll while a QR scan is in progress.
  useEffect(() => {
    if (status !== 'waiting_qr') return undefined;
    const interval = setInterval(loadStatus, 3000);
    return () => clearInterval(interval);
  }, [status, loadStatus]);

  // Toast once when the scan completes.
  useEffect(() => {
    if (status === 'connected' && prevStatus.current && prevStatus.current !== 'connected') {
      toast.success('WhatsApp connected successfully');
    }
    prevStatus.current = status;
  }, [status]);

  const handleConnect = async () => {
    try {
      setConnecting(true);
      connectionStartedRef.current = true;
      setCaptureFailed(false);
      setShowBrowserView(true);
      const { data } = await whatsappApi.connect();
      toast.success(data?.message || 'WhatsApp connection started — scan the QR code');
      setStatus(data?.status || 'waiting_qr');
    } catch (err) {
      connectionStartedRef.current = false;
      setShowBrowserView(false);
      toast.error(getErrorMessage(err, 'Failed to start connection'));
    } finally {
      setConnecting(false);
    }
  };

  const handleCaptureSession = async (force = false) => {
    try {
      setCapturing(true);
      const { data } = await whatsappApi.captureSession(force);
      connectionStartedRef.current = false;
      setCaptureFailed(false);
      setShowBrowserView(false);
      setStatus(data?.status || 'connected');
      toast.success(data?.message || 'WhatsApp session captured — you are connected');
      await loadStatus();
    } catch (err) {
      setCaptureFailed(true);
      toast.error(getErrorMessage(err, 'Could not capture the WhatsApp session'));
    } finally {
      setCapturing(false);
    }
  };

  const handleDisconnect = async () => {
    const confirmed = window.confirm(
      'Disconnect WhatsApp? Active scans and live chat will no longer be able to use this account until you scan a new QR code.',
    );
    if (!confirmed) return;

    try {
      setDisconnecting(true);
      const { data } = await whatsappApi.disconnect();
      connectionStartedRef.current = false;
      setCaptureFailed(false);
      setShowBrowserView(false);
      setStatus('disconnected');
      toast.success(data?.message || 'WhatsApp disconnected successfully');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to disconnect WhatsApp'));
    } finally {
      setDisconnecting(false);
    }
  };

  const connected = status === 'connected';

  return (
    <div className="max-w-3xl">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-zinc-50">WhatsApp Account</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Link WhatsApp Web by scanning the QR code with your phone. The session is
            kept in a dedicated browser profile on our side, so it stays connected
            across restarts and scanner operations.
          </p>
        </div>
        <Link to="/app/account" className="btn-secondary text-xs">
          ← Accounts
        </Link>
      </div>

      {connected ? (
        /* ── Account card (mirrors the LinkedIn manage card) ────── */
        <div className="card mt-6 p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-green-500/10 text-2xl font-bold text-green-300">
                <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 9.75a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375m-13.5 3.01c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 0 1 .778-.332 48.294 48.294 0 0 0 5.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z" />
                </svg>
              </div>
              <div>
                <div className="flex flex-wrap items-center gap-2.5">
                  <h2 className="text-lg font-semibold text-zinc-100">WhatsApp</h2>
                  <WhatsAppStatusBadge status={status} />
                </div>
                <p className="mt-0.5 text-sm text-zinc-400">Linked via WhatsApp Web</p>
              </div>
            </div>
            <button onClick={loadStatus} className="btn-secondary text-xs">
              ↻ Refresh
            </button>
          </div>

          <dl className="mt-5 grid grid-cols-1 gap-4 border-t border-surface-700 pt-5 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs uppercase tracking-wide text-zinc-500">Added</dt>
              <dd className="mt-0.5 text-zinc-300">{formatDate(sessionMeta.created_at)}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-zinc-500">Last updated</dt>
              <dd className="mt-0.5 text-zinc-300">{formatDate(sessionMeta.updated_at)}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-zinc-500">Status details</dt>
              <dd className="mt-0.5 text-zinc-300 capitalize">{status.replace('_', ' ')}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-zinc-500">Session active</dt>
              <dd className="mt-0.5 text-zinc-300">{sessionMeta.is_active ? 'Yes' : 'No'}</dd>
            </div>
          </dl>

          {/* Early-version note: live chat requires jobs to be stopped. */}
          <div className="mt-5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
            <p className="font-medium">⚠️ In early versions, running jobs need to be stopped before using Live Chat</p>
            <p className="mt-1 text-amber-200/80">
              The scanner and live chat share the same WhatsApp session. Pause or stop your
              filter jobs (or wait for them to finish) before opening Live Chat, otherwise
              the scan will pause automatically while chat is open.
            </p>
          </div>

          <div className="mt-6 flex flex-wrap gap-3">
            <Link to="/app/whatsapp-scanner" className="btn-primary">
              WhatsApp Scan
            </Link>
            <Link to="/app/whatsapp-live" className="btn-primary">
              Live Chat
            </Link>
            <button
              type="button"
              onClick={handleDisconnect}
              disabled={disconnecting}
              className="inline-flex items-center gap-2 rounded-lg border border-red-700/50 bg-red-500/10 px-4 py-2 text-sm font-semibold text-red-200 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid="whatsapp-disconnect"
            >
              {disconnecting ? <Spinner /> : null}
              {disconnecting ? 'Disconnecting…' : 'Disconnect WhatsApp'}
            </button>
            <Link to="/app/account" className="btn-secondary">
              ← Back to Accounts
            </Link>
          </div>
        </div>
      ) : (
        <>
          {/* ── Status card (connect flow) ───────────────────────── */}
          <div className="rounded-xl border border-surface-700 bg-surface-800 p-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <h2 className="text-lg font-semibold text-zinc-100">Connection status</h2>
                {loading ? <Spinner /> : <WhatsAppStatusBadge status={status} />}
              </div>
              {status === 'connected' ? (
                <button
                  type="button"
                  onClick={handleDisconnect}
                  disabled={disconnecting}
                  className="inline-flex items-center gap-2 rounded-lg border border-red-700/50 bg-red-500/10 px-4 py-2 text-sm font-semibold text-red-200 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-50"
                  data-testid="whatsapp-disconnect"
                >
                  {disconnecting ? <Spinner /> : null}
                  {disconnecting ? 'Disconnecting…' : 'Disconnect WhatsApp'}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleConnect}
                  disabled={connecting}
                  title={
                    status === 'waiting_qr'
                      ? 'Already scanned but nothing happened? Restart to get a fresh QR code and a fresh watcher.'
                      : undefined
                  }
                  className="inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
                >
                  {connecting ? <Spinner /> : null}
                  {status === 'waiting_qr' ? 'Restart connection' : 'Connect WhatsApp'}
                </button>
              )}
            </div>

            {status === 'waiting_qr' && (
              <>
                <p className="mt-3 text-sm text-yellow-400">
                  The browser is open below — scan the WhatsApp Web QR code with your phone
                  to connect. (It streams live from the server; if it isn't showing yet,
                  wait a moment or press Start in the Live Browser View.)
                </p>

                {/* Manual capture: WhatsApp Web keeps changing its DOM, so the
                    automatic watcher can miss a perfectly good scan. This
                    button snapshots the live session on demand. */}
                <div className="mt-4 rounded-lg border border-accent-500/30 bg-accent-500/5 px-4 py-3">
                  <p className="text-sm font-medium text-zinc-100">
                    Already scanned and your chats are visible below?
                  </p>
                  <p className="mt-1 text-sm text-zinc-400">
                    If the status is still stuck on “Waiting for QR”, press the button to
                    capture the scanned session straight from the open browser.
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-3">
                    <button
                      type="button"
                      onClick={() => handleCaptureSession(false)}
                      disabled={capturing}
                      className="inline-flex items-center gap-2 rounded-lg bg-green-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-green-500 disabled:cursor-not-allowed disabled:opacity-50"
                      data-testid="whatsapp-capture-session"
                    >
                      {capturing ? <Spinner /> : null}
                      {capturing ? 'Capturing session…' : "✅ I've scanned it — capture session"}
                    </button>
                    {captureFailed && (
                      <button
                        type="button"
                        onClick={() => handleCaptureSession(true)}
                        disabled={capturing}
                        className="inline-flex items-center gap-2 rounded-lg border border-surface-600 bg-surface-700 px-4 py-2 text-sm font-semibold text-zinc-200 transition hover:bg-surface-600 disabled:cursor-not-allowed disabled:opacity-50"
                        data-testid="whatsapp-capture-session-force"
                      >
                        Capture anyway
                      </button>
                    )}
                  </div>
                  {captureFailed && (
                    <p className="mt-2 text-xs text-zinc-500">
                      Still not detected? Use “Capture anyway” if WhatsApp is clearly
                      logged in below, or press “Restart connection” for a fresh QR code.
                    </p>
                  )}
                </div>
              </>
            )}
          </div>

          {/* ── Live browser view (QR scan) ──────────────────────── */}
          {!loading && status !== 'connected' && <BrowserViewPanel controls={false} />}
        </>
      )}
    </div>
  );
}
