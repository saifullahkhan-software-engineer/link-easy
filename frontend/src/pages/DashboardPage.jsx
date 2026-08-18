import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { systemQueuesApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';

function Metric({ label, value, tone = 'zinc', detail }) {
  const tones = {
    zinc: 'border-surface-700 bg-surface-900 text-zinc-100',
    emerald: 'border-emerald-500/20 bg-emerald-500/5 text-emerald-200',
    amber: 'border-amber-500/20 bg-amber-500/5 text-amber-200',
    indigo: 'border-indigo-500/20 bg-indigo-500/5 text-indigo-200',
  };

  return (
    <div className={`rounded-xl border p-4 ${tones[tone] || tones.zinc}`}>
      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-zinc-500">{label}</p>
      <p className="mt-3 text-3xl font-bold tracking-tight">{value}</p>
      {detail ? <p className="mt-1 text-xs text-zinc-500">{detail}</p> : null}
    </div>
  );
}

function countStatus(value, status) {
  return Number(value?.[status] || 0);
}

export default function DashboardPage() {
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadOverview = useCallback(async () => {
    try {
      const { data } = await systemQueuesApi.overview();
      setOverview(data);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load the dashboard overview'), { id: 'dashboard-overview' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadOverview();
    const id = setInterval(loadOverview, 15000);
    return () => clearInterval(id);
  }, [loadOverview]);

  if (loading && !overview) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const db = overview?.db || {};
  const redis = overview?.redis || {};
  const celery = overview?.celery || {};
  const queues = overview?.queues || {};
  const activeCampaigns = countStatus(db.campaigns, 'active');
  const activeFeedScans = countStatus(db.feed_scroll, 'active');
  const activeWhatsAppFilters = countStatus(db.whatsapp_filters, 'active');
  const activeTasks = Number(celery.active_count || 0);
  const queueItems = Object.values(queues).reduce((sum, value) => sum + Number(value || 0), 0);
  const workersOnline = (celery.workers || []).length > 0;
  const automationIdle = activeCampaigns === 0 && activeFeedScans === 0 && activeWhatsAppFilters === 0;

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent-400">Operations</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-zinc-50">Application Dashboard</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
            A quick view of automation health and the background services that support your workspace. Open the app to manage accounts and campaigns.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/app" className="btn-primary">Open App <span aria-hidden="true">→</span></Link>
          <Link to="/dashboard/redis-queues" className="btn-secondary">Redis &amp; Queues</Link>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="LinkedIn campaigns"
          value={activeCampaigns}
          tone={activeCampaigns ? 'emerald' : 'zinc'}
          detail={activeCampaigns ? 'active now' : 'no active campaigns'}
        />
        <Metric
          label="Feed scans"
          value={activeFeedScans}
          tone={activeFeedScans ? 'indigo' : 'zinc'}
          detail={activeFeedScans ? 'scheduled jobs' : 'no active scans'}
        />
        <Metric
          label="WhatsApp filters"
          value={activeWhatsAppFilters}
          tone={activeWhatsAppFilters ? 'emerald' : 'zinc'}
          detail={activeWhatsAppFilters ? 'scheduled jobs' : 'no active filters'}
        />
        <Metric
          label="Workers"
          value={workersOnline ? celery.workers.length : 0}
          tone={workersOnline ? 'emerald' : 'amber'}
          detail={workersOnline ? `${activeTasks} active task${activeTasks === 1 ? '' : 's'}` : 'offline or restarting'}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-[1.15fr_0.85fr]">
        <section className="card p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-zinc-100">Automation status</h2>
              <p className="mt-1 text-sm text-zinc-500">Only active, due jobs are allowed to open a browser.</p>
            </div>
            <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${automationIdle ? 'bg-zinc-500/10 text-zinc-300 ring-zinc-500/20' : 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/20'}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${automationIdle ? 'bg-zinc-400' : 'bg-emerald-400 animate-pulse'}`} />
              {automationIdle ? 'Idle' : 'Running'}
            </span>
          </div>
          <div className="mt-6 rounded-xl border border-surface-700 bg-surface-950/70 p-4">
            <p className="text-sm font-medium text-zinc-200">
              {automationIdle ? 'No automation is active.' : 'Automation is currently scheduled.'}
            </p>
            <p className="mt-2 text-sm leading-6 text-zinc-500">
              {automationIdle
                ? 'Paused, deleted, or draft campaigns and filters are ignored by the scheduler. Starting a job from the app is what enables background work.'
                : 'The scheduler reads durable database timestamps and re-checks job status before every browser operation.'}
            </p>
          </div>
          <div className="mt-5 flex flex-wrap gap-2">
            <Link to="/app/campaigns" className="btn-secondary text-xs">Campaign status</Link>
            <Link to="/app/feed-scroll" className="btn-secondary text-xs">Feed scans</Link>
            <Link to="/app/whatsapp-scanner" className="btn-secondary text-xs">WhatsApp filters</Link>
          </div>
        </section>

        <section className="card p-6">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-zinc-100">Service health</h2>
              <p className="mt-1 text-sm text-zinc-500">Current infrastructure snapshot.</p>
            </div>
            <button type="button" onClick={loadOverview} className="btn-secondary px-3 py-1.5 text-xs">Refresh</button>
          </div>
          <div className="mt-5 space-y-3 text-sm">
            <div className="flex items-center justify-between rounded-lg border border-surface-700 bg-surface-950/50 px-3 py-2.5">
              <span className="text-zinc-400">Redis</span>
              <span className={redis.error ? 'text-red-300' : 'text-emerald-300'}>{redis.error ? 'Unavailable' : `${redis.used_memory_human || 'Connected'}`}</span>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-surface-700 bg-surface-950/50 px-3 py-2.5">
              <span className="text-zinc-400">Queue items</span>
              <span className="font-mono text-zinc-200">{queueItems}</span>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-surface-700 bg-surface-950/50 px-3 py-2.5">
              <span className="text-zinc-400">Scheduled tasks</span>
              <span className="font-mono text-zinc-200">{Number(celery.scheduled_count || 0)}</span>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-surface-700 bg-surface-950/50 px-3 py-2.5">
              <span className="text-zinc-400">Reserved tasks</span>
              <span className="font-mono text-zinc-200">{Number(celery.reserved_count || 0)}</span>
            </div>
          </div>
          <Link to="/dashboard/redis-queues" className="mt-5 inline-flex text-sm font-semibold text-accent-300 transition hover:text-accent-200">
            Manage Redis, queues, locks, and jobs <span className="ml-1" aria-hidden="true">→</span>
          </Link>
        </section>
      </div>

      <section className="rounded-xl border border-accent-500/15 bg-accent-500/[0.04] p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-accent-200">Need to manage a stuck task?</h2>
            <p className="mt-1 text-sm text-zinc-500">Use the dashboard operations page to inspect active, queued, paused, and failed work without putting queue controls in the main app.</p>
          </div>
          <Link to="/dashboard/redis-queues" className="btn-secondary text-xs">Open operations</Link>
        </div>
      </section>
    </div>
  );
}
