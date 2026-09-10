import { useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { adminApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../../components/Spinner';
import Modal from '../../components/Modal';
import { Section } from '../../components/admin/shared';
import SettingsEditor from '../../components/admin/SettingsEditor';
import StaleTasksCard from '../../components/admin/StaleTasksCard';

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

const JOB_STATUSES = ['queued', 'running', 'failed', 'skipped', 'done'];

/**
 * Admin: LinkedIn jobs & campaign parameters.
 *
 * Lists the recent campaign (LinkedIn) job audit log, lets the admin tune
 * the campaign/job parameters that govern how those jobs run, and — new —
 * delete jobs that are stuck or no longer needed, even when they never
 * completed. Deleting a job also revokes its Celery task so the worker drops
 * it instead of running it.
 */
export default function AdminLinkedInPage() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(() => new Set());
  const [deletingId, setDeletingId] = useState(null);
  const [statusFilter, setStatusFilter] = useState(() => new Set(['queued', 'running', 'failed']));
  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [cleaning, setCleaning] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await adminApi.linkedinJobs();
      setJobs(data?.jobs || []);
      setSelected(new Set());
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load LinkedIn jobs'), { id: 'admin-li-jobs-load' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const incomplete = useMemo(
    () => jobs.filter((job) => ['queued', 'running', 'failed'].includes(job.status)).length,
    [jobs]
  );

  function toggleSelect(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleStatus(status) {
    setStatusFilter((prev) => {
      const next = new Set(prev);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      return next;
    });
  }

  async function deleteOne(job) {
    if (!window.confirm(`Delete ${job.status} job ${job.id.slice(0, 8)} (${job.step_type})?\n\nIts Celery task is revoked too, so the worker drops it.`)) return;
    setDeletingId(job.id);
    try {
      await adminApi.deleteLinkedInJob(job.id);
      toast.success('Job deleted');
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not delete job'));
    } finally {
      setDeletingId(null);
    }
  }

  function previewPayload() {
    if (selected.size) return { job_ids: [...selected] };
    return { statuses: [...statusFilter] };
  }

  async function openPreview() {
    const payload = previewPayload();
    if (!payload.job_ids?.length && !payload.statuses?.length) {
      toast.error('Select jobs or pick at least one status first');
      return;
    }
    setPreviewLoading(true);
    setPreview(null);
    setPreviewOpen(true);
    try {
      const { data } = await adminApi.bulkDeleteLinkedInJobs({ ...payload, dry_run: true, limit: 500 });
      setPreview({ ...data, payload });
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not preview jobs'));
      setPreviewOpen(false);
    } finally {
      setPreviewLoading(false);
    }
  }

  async function runBulkDelete() {
    if (!preview?.payload) return;
    setCleaning(true);
    try {
      const { data } = await adminApi.bulkDeleteLinkedInJobs({ ...preview.payload, dry_run: false, limit: 500 });
      toast.success(`Deleted ${data.deleted} job(s), revoked ${data.revoked_tasks} task(s)`);
      setPreviewOpen(false);
      setPreview(null);
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not delete jobs'));
    } finally {
      setCleaning(false);
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-amber-400">Administration</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-zinc-50">LinkedIn — Jobs &amp; Parameters</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
            Campaign jobs across all users, plus the campaign parameters and job limits that
            govern how LinkedIn automation runs.
            {incomplete > 0 && (
              <span className="text-amber-300"> {incomplete} incomplete job(s) on this page.</span>
            )}
          </p>
        </div>
        <button type="button" onClick={load} className="btn-secondary px-4 py-2 text-sm">
          Refresh
        </button>
      </div>

      <Section
        title="LinkedIn jobs (campaign audit log)"
        description="Every recorded campaign job, newest first. Stuck or unnecessary leftovers can be deleted even when incomplete."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex flex-wrap gap-1">
              {JOB_STATUSES.map((status) => (
                <button
                  key={status}
                  type="button"
                  onClick={() => toggleStatus(status)}
                  title={statusFilter.has(status) ? `Exclude ${status} from bulk delete` : `Include ${status} in bulk delete`}
                  className={`rounded-full border px-2.5 py-1 text-[11px] font-medium capitalize transition ${
                    statusFilter.has(status)
                      ? 'border-amber-500/30 bg-amber-500/10 text-amber-200'
                      : 'border-surface-700 bg-surface-800 text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  {status}
                </button>
              ))}
            </div>
            {selected.size > 0 && (
              <button type="button" onClick={() => setSelected(new Set())} className="btn-secondary px-3 py-1.5 text-xs">
                Clear ({selected.size})
              </button>
            )}
            <button
              type="button"
              onClick={openPreview}
              className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-amber-500"
            >
              {selected.size ? `Review & delete ${selected.size}…` : 'Review & delete by status…'}
            </button>
          </div>
        }
      >
        {loading ? (
          <div className="flex h-40 items-center justify-center"><Spinner /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[960px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wider text-zinc-500">
                <tr className="border-b border-surface-700">
                  <th className="py-2 pr-4">
                    <input
                      type="checkbox"
                      aria-label="Select all jobs on this page"
                      checked={jobs.length > 0 && selected.size === jobs.length}
                      onChange={(e) => setSelected(e.target.checked ? new Set(jobs.map((j) => j.id)) : new Set())}
                    />
                  </th>
                  <th className="py-2 pr-4">Job</th>
                  <th className="py-2 pr-4">Campaign</th>
                  <th className="py-2 pr-4">Step</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Message</th>
                  <th className="py-2 pr-4">Scheduled</th>
                  <th className="py-2 pr-4">Completed</th>
                  <th className="py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id} className="border-b border-surface-800/70">
                    <td className="py-3 pr-4">
                      <input
                        type="checkbox"
                        aria-label={`Select job ${job.id}`}
                        checked={selected.has(job.id)}
                        onChange={() => toggleSelect(job.id)}
                      />
                    </td>
                    <td className="py-3 pr-4 font-mono text-xs text-zinc-400">{job.id.slice(0, 8)}</td>
                    <td className="py-3 pr-4 text-zinc-300">{job.campaign_name || job.campaign_id}</td>
                    <td className="py-3 pr-4 text-zinc-300">{job.step_type}</td>
                    <td className="py-3 pr-4">
                      <span className="capitalize text-zinc-300">{job.status}</span>
                    </td>
                    <td className="max-w-[220px] truncate py-3 pr-4 text-xs text-zinc-500">
                      {job.action_message || job.error_message || '—'}
                    </td>
                    <td className="py-3 pr-4 text-xs text-zinc-500">{formatDate(job.scheduled_at)}</td>
                    <td className="py-3 pr-4 text-xs text-zinc-500">{formatDate(job.completed_at)}</td>
                    <td className="py-3">
                      <button
                        type="button"
                        onClick={() => deleteOne(job)}
                        disabled={deletingId === job.id}
                        className="rounded-lg border border-red-500/20 bg-red-500/10 px-2.5 py-1.5 text-xs font-semibold text-red-300 transition hover:bg-red-500/20 disabled:opacity-50"
                      >
                        {deletingId === job.id ? 'Deleting…' : 'Delete'}
                      </button>
                    </td>
                  </tr>
                ))}
                {!jobs.length && (
                  <tr>
                    <td colSpan={9} className="py-6 text-center text-zinc-500">No campaign jobs yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 text-xs leading-5 text-zinc-500">
          Deleting a job also revokes its Celery task, so a removed row cannot keep running in the
          worker. Bulk delete defaults to queued / running / failed — toggle the status chips to
          change the set, or tick rows to delete exactly those.
        </p>
      </Section>

      <StaleTasksCard
        scope="linkedin"
        title="Stale LinkedIn queue tasks"
        description="Account-session and campaign tasks still queued even though their campaign is no longer active. Preview first, then revoke the unnecessary leftovers — no jobs are deleted."
      />

      <SettingsEditor
        categories={['campaign', 'jobs']}
        title="Campaign parameters and job limits"
        description="Values are clamped to safe maximums to protect connected LinkedIn accounts."
      />

      {/* Bulk delete preview modal */}
      <Modal open={previewOpen} onClose={() => !cleaning && setPreviewOpen(false)} title="Delete LinkedIn jobs" wide>
        {previewLoading ? (
          <div className="flex h-32 items-center justify-center">
            <Spinner />
          </div>
        ) : preview ? (
          <div className="space-y-4">
            {preview.matched === 0 ? (
              <p className="text-sm text-zinc-400">No jobs match — nothing to delete.</p>
            ) : (
              <>
                <p className="text-sm text-zinc-300">
                  <span className="font-bold text-amber-200">{preview.matched}</span>{' '}
                  {preview.matched === 1 ? 'job' : 'jobs'} will be deleted
                  {preview.payload?.job_ids ? ' (your selection)' : ` (status: ${(preview.payload?.statuses || []).join(', ')})`}:
                </p>
                <div className="max-h-64 overflow-auto rounded-lg border border-surface-700">
                  <table className="w-full text-left text-xs">
                    <thead className="sticky top-0 bg-surface-800 text-[11px] uppercase text-zinc-500">
                      <tr>
                        <th className="px-3 py-2">Job</th>
                        <th className="px-3 py-2">Status</th>
                        <th className="px-3 py-2">Step</th>
                        <th className="px-3 py-2">Created</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-surface-700/50">
                      {preview.sample.map((row) => (
                        <tr key={row.id}>
                          <td className="px-3 py-1.5 font-mono text-zinc-300">{row.id.slice(0, 8)}</td>
                          <td className="px-3 py-1.5 capitalize text-zinc-300">{row.status}</td>
                          <td className="px-3 py-1.5 text-zinc-400">{row.step_type}</td>
                          <td className="px-3 py-1.5 text-zinc-500">{formatDate(row.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {preview.matched > preview.sample.length && (
                  <p className="text-xs text-zinc-500">
                    Showing {preview.sample.length} of {preview.matched} — the rest match the same criteria.
                  </p>
                )}
                <p className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-200">
                  Each job&apos;s Celery task is revoked first, so deleted rows cannot keep running.
                </p>
              </>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPreviewOpen(false)}
                disabled={cleaning}
                className="rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-sm text-zinc-300 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={runBulkDelete}
                disabled={cleaning || preview.matched === 0}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-500 disabled:opacity-50"
              >
                {cleaning ? 'Deleting…' : `Delete ${preview.matched}`}
              </button>
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
