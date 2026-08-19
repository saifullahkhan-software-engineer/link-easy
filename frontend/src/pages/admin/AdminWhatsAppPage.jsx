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
 * Admin: WhatsApp jobs & parameters.
 *
 * Lists every WhatsApp filter job (with message counters) and lets the admin
 * tune the WhatsApp parameters that govern how those jobs scan and forward.
 */
export default function AdminWhatsAppPage() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const { data } = await adminApi.whatsappJobs();
      setJobs(data?.jobs || []);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load WhatsApp jobs'), { id: 'admin-wa-jobs-load' });
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
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-zinc-50">WhatsApp — Jobs &amp; Parameters</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
            WhatsApp filter jobs across all users, plus the parameters that control group
            scanning and forwarding.
          </p>
        </div>
        <button type="button" onClick={load} className="btn-secondary px-4 py-2 text-sm">
          Refresh
        </button>
      </div>

      <Section
        title="WhatsApp jobs (filter jobs)"
        description="Every WhatsApp filter job with its message counters, newest first."
      >
        {loading ? (
          <div className="flex h-40 items-center justify-center"><Spinner /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wider text-zinc-500">
                <tr className="border-b border-surface-700">
                  <th className="py-2 pr-4">Name</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Role / Title</th>
                  <th className="py-2 pr-4">Interval</th>
                  <th className="py-2 pr-4">Total</th>
                  <th className="py-2 pr-4">Matched</th>
                  <th className="py-2 pr-4">Forwarded</th>
                  <th className="py-2 pr-4">Next scan</th>
                  <th className="py-2">Created</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id} className="border-b border-surface-800/70">
                    <td className="py-3 pr-4 font-medium text-zinc-100">{job.name}</td>
                    <td className="py-3 pr-4">
                      <span className="capitalize text-zinc-300">{job.status}</span>
                    </td>
                    <td className="py-3 pr-4 text-xs text-zinc-400">
                      {[job.role, job.job_title].filter(Boolean).join(' · ') || '—'}
                    </td>
                    <td className="py-3 pr-4 text-zinc-300">{job.interval_hours}h</td>
                    <td className="py-3 pr-4 text-zinc-300">{job.total_count}</td>
                    <td className="py-3 pr-4 text-emerald-300">{job.matched_count}</td>
                    <td className="py-3 pr-4 text-zinc-300">{job.forwarded_count}</td>
                    <td className="py-3 pr-4 text-xs text-zinc-500">{formatDate(job.next_scan_at)}</td>
                    <td className="py-3 text-xs text-zinc-500">{formatDate(job.created_at)}</td>
                  </tr>
                ))}
                {!jobs.length && (
                  <tr>
                    <td colSpan={9} className="py-6 text-center text-zinc-500">No WhatsApp filter jobs yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <SettingsEditor
        categories={['whatsapp', 'jobs']}
        title="WhatsApp parameters and job limits"
        description="Values are clamped to safe maximums to protect the connected WhatsApp account."
      />
    </div>
  );
}
