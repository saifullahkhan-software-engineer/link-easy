import { useEffect, useRef, useState } from 'react';
import { liveApi } from '../../api/endpoints';

const LEVEL_STYLES = {
  DEBUG: 'bg-zinc-500/10 text-zinc-400 ring-zinc-500/20',
  INFO: 'bg-sky-500/10 text-sky-300 ring-sky-500/20',
  WARNING: 'bg-yellow-500/10 text-yellow-300 ring-yellow-500/20',
  ERROR: 'bg-red-500/10 text-red-300 ring-red-500/20',
  CRITICAL: 'bg-red-500/10 text-red-300 ring-red-500/20',
};

const MAX_LOGS = 500;

function formatTime(ts) {
  if (!ts) return '--:--:--';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-GB', { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0');
}

/**
 * Real-time console of API-call + application logs, streamed from the
 * backend over SSE (`/api/v1/live/logs`).
 */
export default function LiveLogsPanel() {
  const [logs, setLogs] = useState([]);
  const [connected, setConnected] = useState(false);
  const [paused, setPaused] = useState(false);
  const [levelFilter, setLevelFilter] = useState('ALL');
  const listRef = useRef(null);
  const pendingRef = useRef([]);

  // Keep a mutable buffer so the SSE callback never reads stale state.
  const bufferRef = useRef([]);

  useEffect(() => {
    const es = new EventSource(liveApi.logsStreamUrl());

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    const handleEvent = (e) => {
      try {
        const entry = JSON.parse(e.data);
        bufferRef.current.push(entry);
        if (bufferRef.current.length > MAX_LOGS) {
          bufferRef.current = bufferRef.current.slice(-MAX_LOGS);
        }
        // Batch state updates so a burst of logs doesn't re-render per line.
        if (!pendingRef.current.length) {
          setTimeout(() => {
            setLogs([...bufferRef.current]);
            pendingRef.current = [];
          }, 0);
        }
        pendingRef.current.push(entry);
      } catch {
        // ignore malformed frames
      }
    };

    // Server sends events named after their type (log/app/api/status/ping).
    ['log', 'app', 'api'].forEach((name) => es.addEventListener(name, handleEvent));

    return () => es.close();
  }, []);

  // Auto-scroll to the newest line unless the user is inspecting history.
  useEffect(() => {
    if (!paused && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [logs, paused]);

  const visibleLogs = logs.filter((l) => levelFilter === 'ALL' || l.level === levelFilter);

  return (
    <div className="flex h-full flex-col rounded-xl border border-surface-700 bg-surface-800 p-5">
      {/* Header */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <h2 className="text-lg font-semibold text-zinc-100">Live API Logs</h2>
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
              connected
                ? 'bg-green-500/10 text-green-300 ring-green-500/20'
                : 'bg-red-500/10 text-red-300 ring-red-500/20'
            }`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${connected ? 'bg-green-400 animate-pulse' : 'bg-red-400'}`} />
            {connected ? 'Live' : 'Disconnected'}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <select
            value={levelFilter}
            onChange={(e) => setLevelFilter(e.target.value)}
            className="rounded-lg border border-surface-700 bg-surface-900 px-2.5 py-1.5 text-xs text-zinc-200 focus:border-accent-500 focus:outline-none"
          >
            <option value="ALL">All levels</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
            <option value="DEBUG">DEBUG</option>
          </select>
          <button
            onClick={() => setPaused((p) => !p)}
            className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
              paused
                ? 'border-accent-500 bg-accent-500/10 text-accent-300'
                : 'border-surface-600 bg-surface-700 text-zinc-300 hover:bg-surface-600'
            }`}
          >
            {paused ? 'Resume' : 'Pause'}
          </button>
          <button
            onClick={() => {
              bufferRef.current = [];
              setLogs([]);
            }}
            className="rounded-lg border border-surface-600 bg-surface-700 px-3 py-1.5 text-xs font-medium text-zinc-300 transition hover:bg-surface-600"
          >
            Clear
          </button>
        </div>
      </div>

      {/* Log console */}
      <div
        ref={listRef}
        className="scrollbar-thin h-72 flex-1 space-y-1 overflow-y-auto rounded-lg border border-surface-700 bg-surface-950 p-2.5 font-mono text-[11px] leading-relaxed"
      >
        {visibleLogs.length === 0 ? (
          <p className="py-10 text-center text-xs text-zinc-600">
            {logs.length === 0
              ? 'No logs yet — API calls made by this page will appear here in real time.'
              : 'No logs match the current filter.'}
          </p>
        ) : (
          visibleLogs.map((log, i) => (
            <div key={i} className="flex items-start gap-2 border-b border-surface-800/60 pb-1 last:border-0">
              <span className="shrink-0 text-zinc-600">{formatTime(log.ts)}</span>
              {log.type === 'api' ? (
                <span className="inline-flex shrink-0 items-center rounded bg-accent-500/10 px-1.5 py-px text-[10px] font-semibold text-accent-300">
                  {log.method}
                </span>
              ) : (
                <span
                  className={`inline-flex shrink-0 items-center rounded px-1.5 py-px text-[10px] font-semibold ring-1 ring-inset ${
                    LEVEL_STYLES[log.level] || LEVEL_STYLES.INFO
                  }`}
                >
                  {log.level}
                </span>
              )}
              <span className="min-w-0 break-all text-zinc-300">
                {log.type === 'api' ? (
                  <>
                    <span className="text-zinc-100">{log.path}</span>
                    {log.query ? <span className="text-zinc-500">?{log.query}</span> : null}
                    <span
                      className={`ml-1.5 font-semibold ${
                        log.status >= 500 ? 'text-red-400' : log.status >= 400 ? 'text-yellow-300' : 'text-green-400'
                      }`}
                    >
                      {log.status}
                    </span>
                    <span className="ml-1.5 text-zinc-500">{log.duration_ms}ms</span>
                    {log.user ? <span className="ml-1.5 text-zinc-600">{log.user}</span> : null}
                  </>
                ) : (
                  <span className={log.level === 'ERROR' ? 'text-red-300' : log.level === 'WARNING' ? 'text-yellow-200' : 'text-zinc-300'}>
                    {log.message}
                  </span>
                )}
              </span>
            </div>
          ))
        )}
      </div>

      <p className="mt-2 text-[11px] text-zinc-600">
        Every request hitting <span className="text-zinc-500">/api/v1/*</span> plus in-process app logs are streamed here.
      </p>
    </div>
  );
}
