import { useCallback, useState } from 'react';
import toast from 'react-hot-toast';
import { adminApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../Spinner';
import { Section } from './shared';

/**
 * Admin: stale queued/scheduled task inspector for one scope.
 *
 * Lists Celery tasks that are still reserved/scheduled even though their
 * campaign, feed scan, or filter is no longer active — the "remaining but
 * unnecessary" queue entries — and revokes them on confirmation.
 *
 * Revoking only drops transient queue entries: no job row and (importantly)
 * no filter row is ever deleted. Filters stay attached to their login
 * account whatever happens here.
 */
export default function StaleTasksCard({ scope = 'whatsapp', title, description }) {
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const loadPreview = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await adminApi.stalePreview(scope);
      setPreview(data);
      setLoaded(true);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not preview stale tasks'));
    } finally {
      setLoading(false);
    }
  }, [scope]);

  async function revokeStale() {
    const count = preview?.stale_count || 0;
    if (!count) return;
    if (
      !window.confirm(
        `Revoke ${count} stale ${scope} task(s)?\n\nThis only drops queued/scheduled queue entries. Running browser tasks are not terminated, and no jobs or filters are deleted.`
      )
    ) {
      return;
    }
    setRevoking(true);
    try {
      const { data } = await adminApi.cleanupStaleTasks({ scope, dry_run: false, limit: 200 });
      toast.success(
        `Revoked ${data.revoked_count} stale task(s)` +
          (data.deleted_lease_count ? `, cleared ${data.deleted_lease_count} lease(s)` : '')
      );
      await loadPreview();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not revoke stale tasks'));
    } finally {
      setRevoking(false);
    }
  }

  const stale = preview?.stale || [];

  return (
    <Section
      title={title || 'Stale queued tasks'}
      description={
        description ||
        'Reserved/scheduled queue entries with no active owner. Preview first, then revoke the unnecessary leftovers.'
      }
      actions={
        <div className="flex gap-2">
          <button
            type="button"
            onClick={loadPreview}
            disabled={loading}
            className="btn-secondary px-4 py-2 text-sm disabled:opacity-50"
          >
            {loading ? 'Checking…' : loaded ? 'Re-check' : 'Check stale tasks'}
          </button>
          <button
            type="button"
            onClick={revokeStale}
            disabled={revoking || !stale.length}
            className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-amber-500 disabled:opacity-50"
          >
            {revoking ? 'Revoking…' : `Revoke stale (${preview?.stale_count ?? 0})`}
          </button>
        </div>
      }
    >
      {loading && !loaded ? (
        <div className="flex h-32 items-center justify-center">
          <Spinner />
        </div>
      ) : !loaded ? (
        <p className="py-4 text-center text-sm text-zinc-500">
          Not checked yet — press “Check stale tasks” to inspect the queues. Nothing is changed by checking.
        </p>
      ) : preview?.error ? (
        <p className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-sm text-amber-200">
          Worker inspect unavailable: {preview.error}. The queues cannot be checked while the worker is offline.
        </p>
      ) : !stale.length ? (
        <p className="py-4 text-center text-sm text-zinc-500">
          No stale {scope} tasks — inspected {preview?.inspected ?? 0} queued/scheduled{' '}
          {preview?.inspected === 1 ? 'entry' : 'entries'}, everything has an active owner.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-xs uppercase tracking-wider text-zinc-500">
              <tr className="border-b border-surface-700">
                <th className="py-2 pr-4">Task</th>
                <th className="py-2 pr-4">Target</th>
                <th className="py-2 pr-4">Why stale</th>
                <th className="py-2">Task id</th>
              </tr>
            </thead>
            <tbody>
              {stale.map((task) => (
                <tr key={task.id} className="border-b border-surface-800/70">
                  <td className="py-3 pr-4 font-mono text-xs text-zinc-300">{task.name}</td>
                  <td className="max-w-[220px] truncate py-3 pr-4 font-mono text-xs text-zinc-400">
                    {task.args?.length ? String(task.args[0]) : '—'}
                  </td>
                  <td className="max-w-[320px] py-3 pr-4 text-xs text-amber-200/90">{task.reason}</td>
                  <td className="py-3 font-mono text-xs text-zinc-500">{task.id.slice(0, 8)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-zinc-500">
            Inspected {preview?.inspected ?? 0} queued/scheduled{' '}
            {preview?.inspected === 1 ? 'entry' : 'entries'}
            {preview?.workers?.length ? ` on ${preview.workers.length} worker(s)` : ''}. Active
            browser tasks are never listed or touched.
          </p>
        </div>
      )}
    </Section>
  );
}
