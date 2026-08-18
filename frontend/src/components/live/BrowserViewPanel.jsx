import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { liveApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../Spinner';

const STATUS_STYLES = {
  idle: { bg: 'bg-zinc-500/10', text: 'text-zinc-300', ring: 'ring-zinc-500/20', label: 'Idle', dot: 'bg-zinc-400' },
  starting: { bg: 'bg-yellow-500/10', text: 'text-yellow-300', ring: 'ring-yellow-500/20', label: 'Starting…', dot: 'bg-yellow-400 animate-pulse' },
  running: { bg: 'bg-green-500/10', text: 'text-green-300', ring: 'ring-green-500/20', label: 'Running', dot: 'bg-green-400 animate-pulse' },
  error: { bg: 'bg-red-500/10', text: 'text-red-300', ring: 'ring-red-500/20', label: 'Error', dot: 'bg-red-400' },
};

/**
 * Live view of the backend's embedded Playwright (patchright) browser.
 *
 * Frames are streamed over SSE (`/api/v1/live/browser/stream`) as JPEGs.
 * The view is interactive: clicking or scrolling on the image dispatches the
 * equivalent input into the real browser via `/api/v1/live/browser/input`.
 */
export default function BrowserViewPanel({ controls = true }) {
  const [status, setStatus] = useState('idle');
  const [message, setMessage] = useState('');
  const [error, setError] = useState(null);
  const [frame, setFrame] = useState(null);
  const [connected, setConnected] = useState(false);
  const [starting, setStarting] = useState(false);
  const [interactive, setInteractive] = useState(true);

  const statusStyle = STATUS_STYLES[status] || STATUS_STYLES.idle;

  // ── Stream ────────────────────────────────────────────────────────────────
  useEffect(() => {
    const es = new EventSource(liveApi.browserStreamUrl());

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    es.addEventListener('status', (e) => {
      try {
        const data = JSON.parse(e.data);
        setStatus(data.status);
        setMessage(data.message || '');
        setError(data.error || null);
      } catch {
        // ignore
      }
    });

    es.addEventListener('frame', (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.data) setFrame(`data:image/jpeg;base64,${data.data}`);
      } catch {
        // ignore
      }
    });

    return () => es.close();
  }, []);

  // ── Controls ──────────────────────────────────────────────────────────────
  const handleStart = async () => {
    try {
      setStarting(true);
      const { data } = await liveApi.browserStart();
      setStatus(data.status);
      setMessage(data.message || '');
      setError(data.error || null);
      toast.success('Browser view started');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to start browser view'));
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    try {
      await liveApi.browserStop();
      setStatus('idle');
      setMessage('Browser view stopped');
      setFrame(null);
      toast.success('Browser view stopped');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to stop browser view'));
    }
  };

  const handleCapture = () => {
    // Force a refresh from the server-side frame cache.
    const img = document.getElementById('browser-view-frame');
    if (img) {
      img.src = `${liveApi.browserFrameUrl()}&_=${Date.now()}`;
    }
  };

  // ── Input dispatch ────────────────────────────────────────────────────────
  const normalizedPoint = useCallback((e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
    };
  }, []);

  const handleClick = useCallback(
    (e) => {
      if (!interactive || status !== 'running') return;
      const { x, y } = normalizedPoint(e);
      liveApi.browserInput({ action: 'click', x, y }).catch(() => {});
    },
    [interactive, status, normalizedPoint]
  );

  const handleWheel = useCallback(
    (e) => {
      if (!interactive || status !== 'running') return;
      liveApi.browserInput({ action: 'scroll', deltaY: e.deltaY }).catch(() => {});
    },
    [interactive, status]
  );

  return (
    <div className="flex min-h-[720px] flex-col rounded-xl border border-surface-700 bg-surface-800 p-5">
      {/* Header */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <h2 className="text-lg font-semibold text-zinc-100">Live Browser View</h2>
          <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${statusStyle.bg} ${statusStyle.text} ${statusStyle.ring}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${statusStyle.dot}`} />
            {statusStyle.label}
          </span>
        </div>

        {controls && (
          <div className="flex items-center gap-2">
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-zinc-400">
              <input
                type="checkbox"
                checked={interactive}
                onChange={(e) => setInteractive(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-surface-600 bg-surface-800 text-accent-500 focus:ring-accent-500"
              />
              Click / scroll
            </label>
            <button
              onClick={handleCapture}
              disabled={status !== 'running'}
              className="rounded-lg border border-surface-600 bg-surface-700 px-3 py-1.5 text-xs font-medium text-zinc-300 transition hover:bg-surface-600 disabled:opacity-40"
            >
              Capture
            </button>
            {status === 'running' ? (
              <button
                onClick={handleStop}
                className="rounded-lg border border-red-900/50 bg-red-950/40 px-3 py-1.5 text-xs font-semibold text-red-300 transition hover:bg-red-900/40"
              >
                Stop
              </button>
            ) : (
              <button
                onClick={handleStart}
                disabled={starting}
                className="inline-flex items-center gap-1.5 rounded-lg bg-accent-500 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
              >
                {starting ? <Spinner /> : 'Start'}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Viewport */}
      <div className="relative flex min-h-[560px] flex-1 items-center justify-center overflow-hidden rounded-lg border border-surface-700 bg-surface-950">
        {frame ? (
          <>
            <img
              id="browser-view-frame"
              src={frame}
              alt="Live browser view"
              draggable={false}
              onPointerDown={handleClick}
              onWheel={handleWheel}
              className={`h-full max-h-[78vh] w-full object-contain object-top select-none ${interactive && status === 'running' ? 'cursor-crosshair' : 'cursor-default'}`}
            />
            {status === 'running' && (
              <div className="pointer-events-none absolute left-2 top-2 flex items-center gap-1.5 rounded-full bg-black/50 px-2 py-0.5 text-[10px] text-green-300 backdrop-blur">
                <span className="h-1.5 w-1.5 rounded-full bg-green-400 animate-pulse" />
                REC
              </div>
            )}
          </>
        ) : (
          <div className="flex w-full flex-col items-center justify-center px-6 py-16 text-center">
            {status === 'starting' ? (
              <>
                <Spinner />
                <p className="mt-3 text-sm text-zinc-400">Launching headless Chromium…</p>
              </>
            ) : status === 'error' ? (
              <>
                <p className="text-sm font-semibold text-red-300">Browser view failed to start</p>
                <p className="mt-1 max-w-sm break-words text-xs text-zinc-500">{error || message}</p>
                {controls && (
                  <button
                    onClick={handleStart}
                    className="mt-4 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400"
                  >
                    Retry
                  </button>
                )}
              </>
            ) : (
              <>
                <span className="text-3xl">🖥️</span>
                <p className="mt-3 text-sm text-zinc-300">The Playwright browser runs on the server and streams its screen here.</p>
                <p className="mt-1 text-xs text-zinc-500">
                  {controls ? (
                    <>Click <span className="text-accent-300">Start</span> (or “Connect WhatsApp”) to open the complete WhatsApp Web surface — the QR code and the post-login screen are streamed here for you to use.</>
                  ) : (
                    <>Use <span className="text-accent-300">Connect WhatsApp</span> above. This view will remain open while the QR code and the full post-login screen render.</>
                  )}
                </p>
                {controls && (
                  <button
                    onClick={handleStart}
                    disabled={starting}
                    className="mt-4 inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
                  >
                    {starting ? <Spinner /> : 'Start Browser'}
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* Status line */}
      <p className="mt-2 min-h-[16px] text-[11px] text-zinc-500">
        {connected ? (message || 'Streaming live from the backend browser') : 'Stream disconnected — reconnecting…'}
        {status === 'running' && !connected ? ' (this is normal — frames resume on reconnect)' : ''}
      </p>
    </div>
  );
}
