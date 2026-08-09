import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { whatsappApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';
import WhatsAppStatusBadge from '../components/whatsapp/WhatsAppStatusBadge';
import BrowserViewPanel from '../components/live/BrowserViewPanel';

/**
 * WhatsApp connectivity page (linked from the Accounts hub).
 *
 * This is the ONLY place where the QR connect flow lives — the scanner page
 * just shows the connected status. The connection is kept in a durable
 * server-side browser profile, so it no longer breaks when the scanner
 * opens afterwards.
 */
export default function WhatsAppConnectPage() {
  const [status, setStatus] = useState('disconnected');
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const prevStatus = useRef(null);

  const loadStatus = useCallback(async () => {
    try {
      const { data } = await whatsappApi.getStatus();
      setStatus(data.status || 'disconnected');
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
      const { data } = await whatsappApi.connect();
      toast.success(data?.message || 'WhatsApp connection started — scan the QR code');
      setStatus(data?.status || 'waiting_qr');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to start connection'));
    } finally {
      setConnecting(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <Link
          to="/app/account"
          className="text-xs font-medium text-zinc-500 transition hover:text-zinc-300"
        >
          ← Accounts
        </Link>
        <h1 className="mt-1 text-2xl font-bold text-zinc-100">WhatsApp Connection</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Link WhatsApp Web by scanning the QR code with your phone. The session is
          kept in a dedicated browser profile on our side, so it stays connected
          across restarts and scanner operations.
        </p>
      </div>

      {/* ── Status card ─────────────────────────────────────────── */}
      <div className="rounded-xl border border-surface-700 bg-surface-800 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-zinc-100">Connection status</h2>
            {loading ? <Spinner /> : <WhatsAppStatusBadge status={status} />}
          </div>
          {status !== 'connected' && (
            <button
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
          <p className="mt-3 text-sm text-yellow-400">
            The browser is open below — scan the WhatsApp Web QR code with your phone
            to connect. (It streams live from the server; if it isn't showing yet,
            wait a moment or press Start in the Live Browser View.) If you already
            scanned and the status didn't change, press "Restart connection" to get a
            fresh QR code.
          </p>
        )}

        {status === 'connected' && (
          <div className="mt-4 rounded-lg border border-green-500/30 bg-green-500/10 px-4 py-3 text-sm text-green-200">
            WhatsApp is connected. You can now pick the groups to monitor in the
            scanner.
            <div className="mt-3 flex flex-wrap gap-2">
              <Link to="/app/whatsapp-scanner" className="btn-primary text-xs">
                Open WhatsApp filters →
              </Link>
              <Link to="/app/account" className="btn-secondary text-xs">
                Back to Accounts
              </Link>
            </div>
          </div>
        )}
      </div>

      {/* ── Live browser view (QR scan / 2FA) ───────────────────── */}
      {!loading && status !== 'connected' && <BrowserViewPanel />}
    </div>
  );
}
