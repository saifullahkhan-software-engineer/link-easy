import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { adminApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../../components/Spinner';
import { Section } from '../../components/admin/shared';
import SettingsEditor from '../../components/admin/SettingsEditor';

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
 * Admin: LinkedIn jobs & campaign parameters.
 *
 * Lists the recent campaign (LinkedIn) job audit log and lets the admin tune
 * the campaign/job parameters that govern how those jobs run.
 */
export default function AdminLinkedInPage() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const { data } = await adminApi.linkedinJobs();
      setJobs(data?.jobs || []);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load LinkedIn jobs'), { id: 'admin-li-jobs-load' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-amber-400">Administration</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-zinc-50">LinkedIn — Jobs &amp; Parameters</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
            Campaign jobs across all users, plus the campaign parameters and job limits that
            govern how LinkedIn automation runs.
          </p>
        </div>
        <button type="button" onClick={load} className="btn-secondary px-4 py-2 text-sm">
          Refresh
        </button>
      </div>

      <Section
        title="LinkedIn jobs (campaign audit log)"
        description="Every recorded campaign job, newest first."
      >
        {loading ? (
          <div className="flex h-40 items-center justify-center"><Spinner /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wider text-zinc-500">
                <tr className="border-b border-surface-700">
                  <th className="py-2 pr-4">Job</th>
                  <th className="py-2 pr-4">Campaign</th>
                  <th className="py-2 pr-4">Step</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Message</th>
                  <th className="py-2 pr-4">Scheduled</th>
                  <th className="py-2">Completed</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id} className="border-b border-surface-800/70">
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
                    <td className="py-3 text-xs text-zinc-500">{formatDate(job.completed_at)}</td>
                  </tr>
                ))}
                {!jobs.length && (
                  <tr>
                    <td colSpan={7} className="py-6 text-center text-zinc-500">No campaign jobs yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <SettingsEditor
        categories={['campaign', 'jobs']}
        title="Campaign parameters and job limits"
        description="Values are clamped to safe maximums to protect connected LinkedIn accounts."
      />
    </div>
  );
}
