import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { systemQueuesApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';
import Modal from '../components/Modal';

function timeAgo(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  } catch {
    return iso;
  }
}

function Badge({ children, tone = 'zinc' }) {
  const map = {
    zinc: 'bg-zinc-700/50 text-zinc-300 border-zinc-600/50',
    amber: 'bg-amber-500/10 text-amber-300 border-amber-500/20',
    emerald: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20',
    red: 'bg-red-500/10 text-red-300 border-red-500/20',
    blue: 'bg-blue-500/10 text-blue-300 border-blue-500/20',
    cyan: 'bg-cyan-500/10 text-cyan-300 border-cyan-500/20',
  };
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${map[tone] || map.zinc}`}>
      {children}
    </span>
  );
}

export default function SystemQueuesPage() {
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState(null);
  const [keysData, setKeysData] = useState(null);
  const [keysPattern, setKeysPattern] = useState('*');
  const [keysLoading, setKeysLoading] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState(new Set());
  const [actionLoading, setActionLoading] = useState(null);
  const [showPurgeModal, setShowPurgeModal] = useState(false);
  const [purgeQueue, setPurgeQueue] = useState('celery');
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [filterStatus, setFilterStatus] = useState('all');

  const loadOverview = useCallback(async () => {
    try {
      setLoading(true);
      const { data } = await systemQueuesApi.overview();
      setOverview(data);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load queue overview'));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadKeys = useCallback(async (pattern = keysPattern, offset = 0) => {
    try {
      setKeysLoading(true);
      const { data } = await systemQueuesApi.listRedisKeys({ pattern, limit: 100, offset });
      setKeysData(data);
      setSelectedKeys(new Set());
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load redis keys'));
    } finally {
      setKeysLoading(false);
    }
  }, [keysPattern]);

  useEffect(() => {
    loadOverview();
    loadKeys('*', 0);
  }, [loadOverview, loadKeys]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      loadOverview();
      loadKeys(keysPattern, 0);
    }, 5000);
    return () => clearInterval(id);
  }, [autoRefresh, loadOverview, loadKeys, keysPattern]);

  const handleDeleteSelected = async () => {
    if (selectedKeys.size === 0) return;
    const keys = Array.from(selectedKeys);
    if (!confirm(`Delete ${keys.length} Redis keys?\n\n${keys.slice(0, 10).join('\n')}${keys.length > 10 ? '\n...' : ''}`)) return;
    try {
      setActionLoading('deleteKeys');
      const { data } = await systemQueuesApi.deleteRedisKeys(keys);
      toast.success(`Deleted ${data.deleted} of ${data.requested} keys`);
      if (data.errors?.length) toast.error(`Some errors: ${data.errors.slice(0, 3).map(e => e.error).join(', ')}`);
      loadKeys(keysPattern, 0);
      loadOverview();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to delete keys'));
    } finally {
      setActionLoading(null);
    }
  };

  const handleFlushPattern = async (pattern, dryRun = false) => {
    if (!pattern) return;
    if (!dryRun && !confirm(`Delete ALL keys matching pattern "${pattern}" (up to 100)? This is destructive.`)) return;
    try {
      setActionLoading('flush');
      const { data } = await systemQueuesApi.flushPattern({ pattern, limit: 100, dry_run: dryRun });
      if (dryRun) {
        toast(`Dry run: ${data.matched} keys would be deleted: ${(data.keys || []).slice(0, 5).join(', ')}`, { icon: '🔍' });
      } else {
        toast.success(`Flushed pattern "${pattern}": deleted ${data.deleted} of ${data.matched}`);
        loadKeys(keysPattern, 0);
        loadOverview();
      }
    } catch (err) {
      toast.error(getErrorMessage(err, 'Flush failed'));
    } finally {
      setActionLoading(null);
    }
  };

  const handlePurge = async () => {
    try {
      setActionLoading('purge');
      const { data } = await systemQueuesApi.purgeQueue(purgeQueue === 'all' ? 'all' : purgeQueue);
      toast.success(`Purged queue "${data.queue}": redis=${data.deleted_via_redis} celery=${data.purged_via_celery}`);
      setShowPurgeModal(false);
      loadOverview();
      loadKeys(keysPattern, 0);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Purge failed'));
    } finally {
      setActionLoading(null);
    }
  };

  const handleClearLocks = async (types) => {
    if (!confirm(`Clear locks: ${types.join(', ')}? This will force release browser profile locks & session locks.`)) return;
    try {
      setActionLoading('locks');
      const { data } = await systemQueuesApi.clearLocks(types);
      toast.success(`Cleared ${data.deleted} lock keys`);
      loadOverview();
      loadKeys(keysPattern, 0);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Clear locks failed'));
    } finally {
      setActionLoading(null);
    }
  };

  const handleCleanupStale = async () => {
    if (!confirm('Revoke queued or scheduled automation tasks whose campaign, feed scan, or WhatsApp filter is no longer active? Running browser tasks are not terminated.')) return;
    try {
      setActionLoading('cleanup');
      const { data } = await systemQueuesApi.cleanupStale();
      toast.success(`Cleaned ${data.revoked_count || 0} stale task(s) and ${data.deleted_lease_count || 0} lease(s)`);
      loadOverview();
      loadKeys(keysPattern, 0);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Stale task cleanup failed'));
    } finally {
      setActionLoading(null);
    }
  };

  const handleClearRateLimits = async () => {
    if (!confirm('Clear ALL rate:* keys? This resets daily limits.')) return;
    try {
      setActionLoading('rate');
      const { data } = await systemQueuesApi.clearRateLimits({ pattern: 'rate:*', limit: 1000 });
      toast.success(`Cleared ${data.deleted} rate limit keys (matched ${data.matched})`);
      loadOverview();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Clear rate limits failed'));
    } finally {
      setActionLoading(null);
    }
  };

  const handleDeleteFailedJobs = async (status = 'failed', olderThanDays = null) => {
    const msg = olderThanDays
      ? `Delete campaign jobs with status=${status} older than ${olderThanDays} days?`
      : `Delete ALL campaign jobs with status=${status}? (max 100 at once)`;
    if (!confirm(msg)) return;
    try {
      setActionLoading('bulkDeleteJobs');
      const { data } = await systemQueuesApi.bulkDeleteCampaignJobs({ status, older_than_days: olderThanDays, limit: 100 });
      toast.success(`Deleted ${data.deleted} campaign jobs`);
      loadOverview();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Bulk delete failed'));
    } finally {
      setActionLoading(null);
    }
  };

  const toggleKey = (key) => {
    setSelectedKeys(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  if (loading && !overview) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const redis = overview?.redis || {};
  const queues = overview?.queues || {};
  const locks = overview?.locks || {};
  const rate = overview?.rate_limits || {};
  const celery = overview?.celery || {};
  const db = overview?.db || {};

  // Filter for display: remaining / paused / faulty
  const pausedCampaigns = db.campaigns_paused || [];
  const failedCampaigns = []; // from db.campaigns_failed if exists, else empty
  const activeCampaigns = db.campaigns_active || [];
  const failedJobs = db.campaign_jobs_failed || [];
  const remainingJobs = db.campaign_jobs_remaining || [];
  const feedPaused = (db.feed_scroll_jobs_detailed || []).filter(j => j.status === 'paused');
  const feedActive = (db.feed_scroll_jobs_detailed || []).filter(j => j.status === 'active');
  const feedFailed = (db.feed_scroll_jobs_detailed || []).filter(j => j.status === 'failed');
  const waPaused = (db.whatsapp_filters_detailed || []).filter(j => j.status === 'paused');
  const waActive = (db.whatsapp_filters_detailed || []).filter(j => j.status === 'active');

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/dashboard" className="text-xs font-medium text-zinc-500 transition hover:text-zinc-300">← Dashboard</Link>
          <h1 className="mt-1 text-2xl font-bold text-zinc-50">Redis & Queue Jobs</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Inspect remaining, paused, and faulty jobs in Redis and Postgres — delete unnecessary ones to unblock workers.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
            <span className="rounded bg-surface-800 px-2 py-1 border border-surface-700">
              Redis: {redis.redis_version || 'unknown'} · {redis.used_memory_human || '?'} · {redis.total_keys ?? '?'} keys · {redis.connected_clients ?? '?'} clients
            </span>
            <span className="rounded bg-surface-800 px-2 py-1 border border-surface-700">
              Uptime: {redis.uptime_in_seconds ? `${Math.floor(redis.uptime_in_seconds / 3600)}h` : '?'}
            </span>
            {celery.workers?.length ? (
              <span className="rounded bg-emerald-500/10 px-2 py-1 border border-emerald-500/20 text-emerald-300">
                Workers: {celery.workers.join(', ')} · Active {celery.active_count} · Scheduled {celery.scheduled_count} · Reserved {celery.reserved_count}
              </span>
            ) : (
              <span className="rounded bg-amber-500/10 px-2 py-1 border border-amber-500/20 text-amber-300">
                No celery workers reporting (offline or restarting)
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-zinc-400">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} className="rounded" />
            Auto-refresh 5s
          </label>
          <button
            onClick={handleCleanupStale}
            disabled={actionLoading === 'cleanup'}
            className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-1.5 text-xs font-semibold text-amber-300 transition hover:bg-amber-500/10 disabled:opacity-50"
            title="Revoke queued/scheduled automation that no longer has an active database job"
          >
            {actionLoading === 'cleanup' ? 'Cleaning…' : 'Clean stale automation'}
          </button>
          <button onClick={() => { loadOverview(); loadKeys(keysPattern, 0); }} className="btn-secondary text-xs">
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Top stats cards */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <div className="card p-4">
          <p className="text-xs uppercase tracking-wide text-zinc-500 font-semibold">Celery Queues</p>
          <div className="mt-2 space-y-1">
            {Object.keys(queues).length === 0 ? (
              <p className="text-sm text-zinc-400">No pending queue items</p>
            ) : (
              Object.entries(queues).map(([q, len]) => (
                <div key={q} className="flex justify-between text-sm">
                  <span className="font-mono text-zinc-300">{q}</span>
                  <span className={`font-bold ${len > 0 ? 'text-amber-300' : 'text-zinc-500'}`}>{len}</span>
                </div>
              ))
            )}
          </div>
          <button onClick={() => setShowPurgeModal(true)} className="mt-3 w-full rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-1.5 text-xs font-semibold text-amber-300 hover:bg-amber-500/10">
            Purge Queue…
          </button>
        </div>

        <div className="card p-4">
          <p className="text-xs uppercase tracking-wide text-zinc-500 font-semibold">Locks (block browser)</p>
          <div className="mt-2 text-xs space-y-1">
            <div>Session locks: <b className="text-zinc-200">{locks.session_locks?.length || 0}</b></div>
            <div>Profile locks: <b className="text-zinc-200">{locks.profile_locks?.length || 0}</b></div>
            <div>Semaphore: <b className="text-zinc-200">{locks.playwright_semaphore?.count ?? 0} / 2</b></div>
            <div>Other locks: <b className="text-zinc-200">{locks.other_locks?.length || 0}</b></div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-1.5">
            <button disabled={actionLoading === 'locks'} onClick={() => handleClearLocks(['session'])} className="rounded bg-surface-800 border border-surface-700 px-2 py-1 text-[11px] text-zinc-300 hover:bg-surface-700">
              Clear session locks
            </button>
            <button disabled={actionLoading === 'locks'} onClick={() => handleClearLocks(['profile'])} className="rounded bg-surface-800 border border-surface-700 px-2 py-1 text-[11px] text-zinc-300 hover:bg-surface-700">
              Clear profile locks
            </button>
            <button disabled={actionLoading === 'locks'} onClick={() => handleClearLocks(['semaphore'])} className="rounded bg-surface-800 border border-surface-700 px-2 py-1 text-[11px] text-zinc-300 hover:bg-surface-700">
              Clear semaphore
            </button>
            <button disabled={actionLoading === 'locks'} onClick={() => handleClearLocks(['session', 'profile', 'semaphore'])} className="rounded bg-red-500/10 border border-red-500/20 px-2 py-1 text-[11px] text-red-300 hover:bg-red-500/20">
              Clear ALL locks
            </button>
          </div>
        </div>

        <div className="card p-4">
          <p className="text-xs uppercase tracking-wide text-zinc-500 font-semibold">Rate Limits</p>
          <div className="mt-2 text-xs">
            <div>Total rate keys: <b className="text-zinc-200">{rate.count ?? 0}</b></div>
            <div className="mt-1 flex flex-wrap gap-1">
              {Object.entries(rate.by_action || {}).map(([act, cnt]) => (
                <Badge key={act} tone="cyan">{act}: {cnt}</Badge>
              ))}
            </div>
          </div>
          <button disabled={actionLoading === 'rate'} onClick={handleClearRateLimits} className="mt-3 w-full rounded-lg border border-surface-700 bg-surface-800 px-3 py-1.5 text-xs font-semibold text-zinc-300 hover:bg-surface-700">
            Clear rate:* keys
          </button>
        </div>

        <div className="card p-4">
          <p className="text-xs uppercase tracking-wide text-zinc-500 font-semibold">DB Jobs Summary</p>
          <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
            <div className="rounded bg-surface-800 p-2 border border-surface-700">
              <div className="text-zinc-500">Campaigns</div>
              <div className="mt-1 flex gap-1 flex-wrap">
                {Object.entries(db.campaigns || {}).map(([st, cnt]) => (
                  <Badge key={st} tone={st === 'paused' ? 'amber' : st === 'failed' ? 'red' : st === 'active' ? 'emerald' : 'zinc'}>{st}: {cnt}</Badge>
                ))}
              </div>
            </div>
            <div className="rounded bg-surface-800 p-2 border border-surface-700">
              <div className="text-zinc-500">Campaign Jobs</div>
              <div className="mt-1 flex gap-1 flex-wrap">
                {Object.entries(db.campaign_jobs || {}).map(([st, cnt]) => (
                  <Badge key={st} tone={st === 'failed' ? 'red' : st === 'running' ? 'blue' : 'zinc'}>{st}: {cnt}</Badge>
                ))}
              </div>
            </div>
            <div className="rounded bg-surface-800 p-2 border border-surface-700">
              <div className="text-zinc-500">Feed Scroll</div>
              <div className="mt-1 flex gap-1 flex-wrap">
                {Object.entries(db.feed_scroll || {}).map(([st, cnt]) => (
                  <Badge key={st} tone={st === 'paused' ? 'amber' : st === 'failed' ? 'red' : 'emerald'}>{st}: {cnt}</Badge>
                ))}
              </div>
            </div>
            <div className="rounded bg-surface-800 p-2 border border-surface-700">
              <div className="text-zinc-500">WhatsApp</div>
              <div className="mt-1 flex gap-1 flex-wrap">
                {Object.entries(db.whatsapp_filters || {}).map(([st, cnt]) => (
                  <Badge key={st} tone={st === 'paused' ? 'amber' : st === 'failed' ? 'red' : 'emerald'}>{st}: {cnt}</Badge>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Tabs for paused / remaining / faulty */}
      <div className="card p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-300">Paused / Remaining / Faulty (DB)</h2>
          <div className="flex gap-1">
            {['all', 'paused', 'remaining', 'failed'].map(tab => (
              <button
                key={tab}
                onClick={() => setFilterStatus(tab)}
                className={`rounded-full px-3 py-1 text-xs font-medium border ${filterStatus === tab ? 'bg-accent-500/10 text-accent-300 border-accent-500/20' : 'bg-surface-800 text-zinc-400 border-surface-700 hover:text-zinc-200'}`}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* Paused campaigns */}
          {(filterStatus === 'all' || filterStatus === 'paused') && (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
              <h3 className="text-xs font-semibold text-amber-300">⏸ Paused Campaigns ({pausedCampaigns.length})</h3>
              {pausedCampaigns.length === 0 ? (
                <p className="mt-2 text-xs text-zinc-500">No paused campaigns</p>
              ) : (
                <div className="mt-2 max-h-64 overflow-auto space-y-2">
                  {pausedCampaigns.map(c => (
                    <div key={c.id} className="flex items-center justify-between gap-2 rounded bg-surface-900 border border-surface-700/50 px-3 py-2">
                      <div className="min-w-0">
                        <div className="truncate text-xs font-medium text-zinc-100">{c.name}</div>
                        <div className="truncate text-[11px] text-zinc-500">{c.account_email} · {timeAgo(c.created_at)}</div>
                      </div>
                      <Badge tone="amber">{c.id.slice(0, 8)}</Badge>
                    </div>
                  ))}
                </div>
              )}

              <h3 className="mt-4 text-xs font-semibold text-amber-300">⏸ Paused Feed Scroll ({feedPaused.length})</h3>
              {feedPaused.length === 0 ? <p className="mt-1 text-xs text-zinc-500">None</p> : (
                <div className="mt-2 max-h-32 overflow-auto space-y-1">
                  {feedPaused.map(j => (
                    <div key={j.id} className="flex justify-between text-xs bg-surface-900 border border-surface-700/50 rounded px-2 py-1">
                      <span className="text-zinc-300">{j.name || j.id.slice(0, 8)}</span>
                      <span className="text-zinc-500">{j.owner_email}</span>
                    </div>
                  ))}
                </div>
              )}

              <h3 className="mt-4 text-xs font-semibold text-amber-300">⏸ Paused WhatsApp Filters ({waPaused.length})</h3>
              {waPaused.length === 0 ? <p className="mt-1 text-xs text-zinc-500">None</p> : (
                <div className="mt-2 max-h-32 overflow-auto space-y-1">
                  {waPaused.map(j => (
                    <div key={j.id} className="flex justify-between text-xs bg-surface-900 border border-surface-700/50 rounded px-2 py-1">
                      <span className="text-zinc-300">{String(j.id).slice(0, 8)}</span>
                      <span className="text-zinc-500">{j.name}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Remaining jobs */}
          {(filterStatus === 'all' || filterStatus === 'remaining') && (
            <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 p-3">
              <h3 className="text-xs font-semibold text-blue-300">⏳ Remaining / Queued Jobs ({remainingJobs.length})</h3>
              {remainingJobs.length === 0 ? (
                <p className="mt-2 text-xs text-zinc-500">No queued/running jobs in DB</p>
              ) : (
                <div className="mt-2 max-h-64 overflow-auto space-y-2">
                  {remainingJobs.map(j => (
                    <div key={j.id} className="rounded bg-surface-900 border border-surface-700/50 px-3 py-2">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-medium text-zinc-200">{j.step_type}</span>
                        <Badge tone={j.status === 'running' ? 'blue' : 'zinc'}>{j.status}</Badge>
                      </div>
                      <div className="mt-1 text-[11px] text-zinc-500 truncate">
                        campaign {j.campaign_id.slice(0, 8)} · lead {j.lead_id.slice(0, 8)} · {timeAgo(j.created_at)}
                      </div>
                      {j.celery_task_id && (
                        <div className="mt-1 flex gap-2">
                          <span className="text-[10px] font-mono text-zinc-600 truncate">{j.celery_task_id}</span>
                          <button
                            onClick={async () => {
                              if (!confirm(`Revoke celery task ${j.celery_task_id}?`)) return;
                              try {
                                await systemQueuesApi.revokeTask(j.celery_task_id);
                                toast.success('Task revoked');
                                loadOverview();
                              } catch (e) { toast.error(getErrorMessage(e)); }
                            }}
                            className="text-[10px] text-red-400 hover:text-red-300"
                          >
                            revoke
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <h3 className="mt-4 text-xs font-semibold text-blue-300">⏳ Active Campaigns ({activeCampaigns.length}) & Feed/WhatsApp Active</h3>
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                <div>
                  <div className="text-zinc-500">Feed active: {feedActive.length}</div>
                  {feedActive.map(j => <div key={j.id} className="truncate text-zinc-400">{j.name || j.id.slice(0, 8)}</div>)}
                </div>
                <div>
                  <div className="text-zinc-500">WhatsApp active: {waActive.length}</div>
                  {waActive.map(j => <div key={j.id} className="truncate text-zinc-400">{String(j.id).slice(0, 8)} {j.name}</div>)}
                </div>
              </div>

              {celery.raw && (
                <div className="mt-3">
                  <h4 className="text-[11px] font-semibold text-zinc-400">Celery Inspect (if worker online)</h4>
                  <div className="mt-1 max-h-40 overflow-auto rounded bg-surface-950 border border-surface-800 p-2 text-[11px] font-mono text-zinc-400">
                    <div>Active: {celery.active_count} · Scheduled: {celery.scheduled_count} · Reserved: {celery.reserved_count}</div>
                    {celery.raw?.active && Object.entries(celery.raw.active).map(([worker, tasks]) => (
                      <div key={worker} className="mt-1">
                        <div className="text-amber-300">{worker} ({Array.isArray(tasks) ? tasks.length : 0} active)</div>
                        {(Array.isArray(tasks) ? tasks.slice(0, 5) : []).map((t, i) => (
                          <div key={i} className="truncate">{t?.name || t?.type} · {t?.id?.slice(0, 8)} · arg {JSON.stringify(t?.args).slice(0, 80)}</div>
                        ))}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Faulty */}
        {(filterStatus === 'all' || filterStatus === 'failed') && (
          <div className="mt-4 rounded-lg border border-red-500/20 bg-red-500/5 p-3">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold text-red-300">❌ Faulty / Failed Jobs ({failedJobs.length})</h3>
              <div className="flex gap-2">
                <button
                  disabled={actionLoading === 'bulkDeleteJobs'}
                  onClick={() => handleDeleteFailedJobs('failed')}
                  className="rounded bg-red-500/10 border border-red-500/20 px-2 py-1 text-[11px] text-red-300 hover:bg-red-500/20"
                >
                  Delete 100 failed
                </button>
                <button
                  disabled={actionLoading === 'bulkDeleteJobs'}
                  onClick={() => handleDeleteFailedJobs('failed', 7)}
                  className="rounded bg-surface-800 border border-surface-700 px-2 py-1 text-[11px] text-zinc-300 hover:bg-surface-700"
                >
                  Delete failed &gt;7d
                </button>
              </div>
            </div>
            {failedJobs.length === 0 ? (
              <p className="mt-2 text-xs text-zinc-500">No failed jobs</p>
            ) : (
              <div className="mt-2 max-h-96 overflow-auto space-y-2">
                {failedJobs.map(j => (
                  <div key={j.id} className="rounded bg-surface-900 border border-red-500/10 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-zinc-200">{j.step_type} · {j.campaign_id.slice(0, 8)}</span>
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-zinc-500">{timeAgo(j.created_at)}</span>
                        <button
                          onClick={async () => {
                            if (!confirm(`Delete failed job ${j.id}?`)) return;
                            try {
                              await systemQueuesApi.deleteCampaignJob(j.id);
                              toast.success('Deleted');
                              loadOverview();
                            } catch (e) { toast.error(getErrorMessage(e)); }
                          }}
                          className="text-[11px] text-red-400 hover:text-red-300"
                        >
                          delete
                        </button>
                      </div>
                    </div>
                    <div className="mt-1 text-[11px] text-red-300 line-clamp-2">{j.error_message || j.action_message || 'No error message'}</div>
                    {j.celery_task_id && (
                      <div className="mt-1 text-[10px] font-mono text-zinc-600 truncate">{j.celery_task_id}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Redis keys explorer */}
      <div className="card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-300">Redis Keys — remaining jobs in Redis</h2>
          <div className="flex items-center gap-2">
            <input
              value={keysPattern}
              onChange={(e) => setKeysPattern(e.target.value)}
              placeholder="Pattern e.g. celery*, session_lock:*, rate:*, profile_lock:*"
              className="w-72 rounded-lg border border-surface-700 bg-surface-800 px-3 py-1.5 text-xs text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
            />
            <button
              onClick={() => loadKeys(keysPattern, 0)}
              disabled={keysLoading}
              className="rounded-lg bg-surface-800 border border-surface-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-surface-700"
            >
              {keysLoading ? <Spinner /> : 'Search'}
            </button>
            <button
              onClick={() => handleFlushPattern(keysPattern, true)}
              disabled={actionLoading === 'flush'}
              className="rounded-lg bg-surface-800 border border-surface-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-surface-700"
            >
              Dry-run
            </button>
            <button
              onClick={() => handleFlushPattern(keysPattern, false)}
              disabled={actionLoading === 'flush'}
              className="rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/20"
            >
              Delete matching
            </button>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          {['*', 'celery*', 'session_lock:*', 'profile_lock:*', 'playwright:*', 'rate:*', '*lock*', 'unacked*', '_kombu*'].map(p => (
            <button
              key={p}
              onClick={() => { setKeysPattern(p); loadKeys(p, 0); }}
              className={`rounded-full px-2.5 py-1 border text-[11px] ${keysPattern === p ? 'bg-accent-500/10 text-accent-300 border-accent-500/20' : 'bg-surface-800 text-zinc-400 border-surface-700 hover:text-zinc-200'}`}
            >
              {p}
            </button>
          ))}
        </div>

        {keysData && (
          <div className="mt-4">
            <div className="flex items-center justify-between text-xs text-zinc-500">
              <span>
                Total matched: <b className="text-zinc-200">{keysData.total_matched}</b> · Showing {keysData.keys.length} (offset {keysData.offset})
                {selectedKeys.size > 0 && <span className="ml-2 text-accent-300">· {selectedKeys.size} selected</span>}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setSelectedKeys(new Set(keysData.keys.map(k => k.key)))}
                  className="rounded bg-surface-800 border border-surface-700 px-2 py-1 text-[11px] hover:bg-surface-700"
                >
                  Select all in view
                </button>
                <button
                  onClick={() => setSelectedKeys(new Set())}
                  className="rounded bg-surface-800 border border-surface-700 px-2 py-1 text-[11px] hover:bg-surface-700"
                >
                  Clear selection
                </button>
                <button
                  onClick={handleDeleteSelected}
                  disabled={selectedKeys.size === 0 || actionLoading === 'deleteKeys'}
                  className="rounded bg-red-500/10 border border-red-500/20 px-2 py-1 text-[11px] text-red-300 hover:bg-red-500/20 disabled:opacity-50"
                >
                  Delete selected ({selectedKeys.size})
                </button>
              </div>
            </div>

            <div className="mt-3 overflow-auto rounded-lg border border-surface-700 max-h-[420px]">
              <table className="min-w-full text-xs">
                <thead className="sticky top-0 bg-surface-800 text-zinc-400 text-[11px] uppercase">
                  <tr>
                    <th className="px-3 py-2 text-left">
                      <input
                        type="checkbox"
                        checked={selectedKeys.size === keysData.keys.length && keysData.keys.length > 0}
                        onChange={(e) => {
                          if (e.target.checked) setSelectedKeys(new Set(keysData.keys.map(k => k.key)));
                          else setSelectedKeys(new Set());
                        }}
                      />
                    </th>
                    <th className="px-3 py-2 text-left">Key</th>
                    <th className="px-3 py-2 text-left">Type</th>
                    <th className="px-3 py-2 text-left">Size / Len</th>
                    <th className="px-3 py-2 text-left">TTL</th>
                    <th className="px-3 py-2 text-left">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-surface-700/50">
                  {keysData.keys.map((k) => (
                    <tr key={k.key} className="hover:bg-surface-800/50">
                      <td className="px-3 py-1.5">
                        <input
                          type="checkbox"
                          checked={selectedKeys.has(k.key)}
                          onChange={() => toggleKey(k.key)}
                        />
                      </td>
                      <td className="px-3 py-1.5 font-mono text-zinc-300 max-w-[360px] truncate" title={k.key}>
                        {k.key}
                      </td>
                      <td className="px-3 py-1.5">
                        <Badge tone="zinc">{k.type}</Badge>
                      </td>
                      <td className="px-3 py-1.5 text-zinc-400">{k.size ?? '—'}</td>
                      <td className="px-3 py-1.5 text-zinc-500">
                        {k.ttl === -1 ? 'no expiry' : k.ttl === -2 ? 'expired' : `${k.ttl}s`}
                      </td>
                      <td className="px-3 py-1.5">
                        <button
                          onClick={async () => {
                            if (!confirm(`Delete key "${k.key}"?`)) return;
                            try {
                              await systemQueuesApi.deleteRedisKeys([k.key]);
                              toast.success(`Deleted ${k.key}`);
                              loadKeys(keysPattern, 0);
                              loadOverview();
                            } catch (e) { toast.error(getErrorMessage(e)); }
                          }}
                          className="text-[11px] text-red-400 hover:text-red-300"
                        >
                          delete
                        </button>
                      </td>
                    </tr>
                  ))}
                  {keysData.keys.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-3 py-6 text-center text-zinc-500">
                        No keys matched pattern "{keysData.pattern}"
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="mt-3 flex gap-2">
              <button
                disabled={keysData.offset === 0}
                onClick={() => loadKeys(keysPattern, Math.max(0, keysData.offset - keysData.limit))}
                className="rounded bg-surface-800 border border-surface-700 px-3 py-1 text-xs disabled:opacity-50"
              >
                ← Prev
              </button>
              <button
                disabled={keysData.offset + keysData.limit >= keysData.total_matched}
                onClick={() => loadKeys(keysPattern, keysData.offset + keysData.limit)}
                className="rounded bg-surface-800 border border-surface-700 px-3 py-1 text-xs disabled:opacity-50"
              >
                Next →
              </button>
              <span className="text-xs text-zinc-500 self-center">
                {keysData.offset + 1}–{Math.min(keysData.offset + keysData.limit, keysData.total_matched)} of {keysData.total_matched}
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Purge modal */}
      <Modal open={showPurgeModal} onClose={() => setShowPurgeModal(false)} title="Purge Celery Queue">
        <div className="space-y-4">
          <p className="text-xs text-zinc-400">Purging removes all pending tasks from the queue in Redis (and asks workers to discard them). Use this to delete unnecessary remaining jobs.</p>
          <div>
            <label className="input-label">Queue name</label>
            <select
              value={purgeQueue}
              onChange={(e) => setPurgeQueue(e.target.value)}
              className="mt-1 w-full rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-sm text-zinc-200"
            >
              <option value="celery">celery (default)</option>
              <option value="default">default</option>
              <option value="linkedin_sessions">linkedin_sessions</option>
              <option value="all">all (celery + default + linkedin_sessions)</option>
            </select>
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setShowPurgeModal(false)} className="rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-sm text-zinc-300">Cancel</button>
            <button disabled={actionLoading === 'purge'} onClick={handlePurge} className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-500">
              {actionLoading === 'purge' ? 'Purging…' : 'Purge queue'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
