import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { adminApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../../components/Spinner';
import { Metric, Section, StatusPills } from '../../components/admin/shared';

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
 * Admin: Accounts.
 *
 * Lists every LinkedIn account and WhatsApp session across all users, plus
 * summary metrics. This is the /admin/accounts module of the admin sidebar
 * (Accounts → Users → LinkedIn → WhatsApp).
 */
export default function AdminAccountsPage() {
  const [overview, setOverview] = useState(null);
  const [accounts, setAccounts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [removingSession, setRemovingSession] = useState(null);

  const load = useCallback(async () => {
    try {
      const [ov, ac] = await Promise.all([adminApi.overview(), adminApi.accounts()]);
      setOverview(ov.data);
      setAccounts(ac.data);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load accounts'), { id: 'admin-accounts-load' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function removeWhatsAppSession(row) {
    if (!window.confirm(`Remove WhatsApp session #${row.id}? This disconnects it and deletes its saved credentials.`)) return;
    setRemovingSession(row.id);
    try {
      await adminApi.deleteWhatsAppSession(row.id);
      toast.success(`WhatsApp session #${row.id} removed`);
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not remove WhatsApp session'));
    } finally {
      setRemovingSession(null);
    }
  }

  if (loading && !accounts) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const a = overview?.accounts || {};
  const counts = accounts?.counts || {};
  const linkedin = accounts?.linkedin || [];
  const whatsapp = accounts?.whatsapp || [];

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-amber-400">Administration</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-zinc-50">Accounts</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
            Every LinkedIn account and WhatsApp session, with their current status.
          </p>
        </div>
        <button type="button" onClick={load} className="btn-secondary px-4 py-2 text-sm">
          Refresh
        </button>
      </div>

      {/* Summary metrics */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="LinkedIn accounts" value={counts.linkedin_total ?? a.linkedin_total ?? 0} tone="indigo" detail={`${counts.linkedin_active ?? 0} active`} />
        <Metric label="WhatsApp sessions" value={counts.whatsapp_total ?? a.whatsapp_total ?? 0} tone="emerald" detail={`${counts.whatsapp_connected ?? a.whatsapp_connected ?? 0} connected`} />
        <Metric label="Users" value={overview?.users?.total ?? 0} detail={`${overview?.users?.admins ?? 0} admin`} />
        <Metric label="Campaigns" value={overview?.jobs?.campaigns_total ?? 0} tone="amber" detail="across all users" />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="LinkedIn accounts by status" description="Health of connected LinkedIn profiles.">
          <StatusPills map={a.linkedin_by_status} />
        </Section>
        <Section title="WhatsApp sessions" description="WhatsApp connection health.">
          <div className="flex flex-wrap gap-2">
            <span className="inline-flex items-center gap-2 rounded-lg border border-surface-700 bg-surface-800 px-3 py-1.5 text-xs text-zinc-300">
              <span className="font-medium text-zinc-400">connected</span>
              <span className="font-bold text-zinc-100">{counts.whatsapp_connected ?? 0}</span>
            </span>
            <span className="inline-flex items-center gap-2 rounded-lg border border-surface-700 bg-surface-800 px-3 py-1.5 text-xs text-zinc-300">
              <span className="font-medium text-zinc-400">total</span>
              <span className="font-bold text-zinc-100">{counts.whatsapp_total ?? 0}</span>
            </span>
          </div>
        </Section>
      </div>

      {/* LinkedIn accounts table */}
      <Section title="LinkedIn accounts" description="All LinkedIn accounts (newest first).">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="text-xs uppercase tracking-wider text-zinc-500">
              <tr className="border-b border-surface-700">
                <th className="py-2 pr-4">LinkedIn email</th>
                <th className="py-2 pr-4">Owner</th>
                <th className="py-2 pr-4">Label</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Added</th>
                <th className="py-2 pr-4">Last updated</th>
                <th className="py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {linkedin.map((row) => (
                <tr key={row.id} className="border-b border-surface-800/70">
                  <td className="py-3 pr-4 font-medium text-zinc-100">{row.linkedin_email}</td>
                  <td className="py-3 pr-4 text-xs text-zinc-400">{row.owner_email || '—'}</td>
                  <td className="py-3 pr-4 text-zinc-300">{row.label || '—'}</td>
                  <td className="py-3 pr-4">
                    <span className="capitalize text-zinc-300">{row.status || '—'}</span>
                  </td>
                  <td className="py-3 pr-4 text-xs text-zinc-500">{formatDate(row.created_at)}</td>
                  <td className="py-3 pr-4 text-xs text-zinc-500">{formatDate(row.updated_at)}</td>
                  <td className="py-3"><button type="button" onClick={() => removeWhatsAppSession(row)} disabled={removingSession === row.id} className="rounded-lg border border-red-500/20 bg-red-500/10 px-2.5 py-1.5 text-xs font-semibold text-red-300 transition hover:bg-red-500/20 disabled:opacity-50">{removingSession === row.id ? 'Removing…' : 'Remove'}</button></td>
                </tr>
              ))}
              {!linkedin.length && (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-zinc-500">No LinkedIn accounts yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>

      {/* WhatsApp sessions table */}
      <Section title="WhatsApp sessions" description="All WhatsApp sessions (newest first).">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-xs uppercase tracking-wider text-zinc-500">
              <tr className="border-b border-surface-700">
                <th className="py-2 pr-4">Session</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Active</th>
                <th className="py-2 pr-4">Added</th>
                <th className="py-2">Last updated</th>
              </tr>
            </thead>
            <tbody>
              {whatsapp.map((row) => (
                <tr key={row.id} className="border-b border-surface-800/70">
                  <td className="py-3 pr-4 font-mono text-xs text-zinc-300">session #{row.id}</td>
                  <td className="py-3 pr-4 capitalize text-zinc-300">{row.status}</td>
                  <td className="py-3 pr-4">
                    <span className={row.is_active ? 'text-emerald-300' : 'text-zinc-500'}>
                      {row.is_active ? 'Yes' : 'No'}
                    </span>
                  </td>
                  <td className="py-3 pr-4 text-xs text-zinc-500">{formatDate(row.created_at)}</td>
                  <td className="py-3 text-xs text-zinc-500">{formatDate(row.updated_at)}</td>
                </tr>
              ))}
              {!whatsapp.length && (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-zinc-500">No WhatsApp sessions yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
