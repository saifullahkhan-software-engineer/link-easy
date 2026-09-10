import { useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { adminApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../../components/Spinner';
import Modal from '../../components/Modal';
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

/** Amber pill shown when the DB says "usable" but the durable browser profile
 *  was wiped (usually: the /app/profiles volume is not mounted). */
function ProfileMissingPill({ title }) {
  return (
    <span
      title={title}
      className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-300 ring-1 ring-inset ring-amber-500/20"
    >
      profile missing
    </span>
  );
}

/** A session is "closed" when it can no longer do work: not connected, or
 *  flagged inactive. Mirrors the backend's CLOSED_SESSION_STATUSES. */
function isClosedSession(row) {
  return row.status !== 'connected' || !row.is_active;
}

/**
 * Admin: Accounts.
 *
 * Lists every LinkedIn account and WhatsApp session across all users, plus
 * summary metrics. Closed (disconnected/error/QR/inactive) WhatsApp sessions
 * can be removed one by one or in bulk.
 *
 * Removing a session NEVER deletes its filter jobs: filters belong to the
 * login account that owns them and are only unlinked from the removed device.
 */
export default function AdminAccountsPage() {
  const [overview, setOverview] = useState(null);
  const [accounts, setAccounts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [removingSession, setRemovingSession] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [cleaning, setCleaning] = useState(false);

  const load = useCallback(async () => {
    try {
      const [ov, ac] = await Promise.all([adminApi.overview(), adminApi.accounts()]);
      setOverview(ov.data);
      setAccounts(ac.data);
      setSelected(new Set());
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load accounts'), { id: 'admin-accounts-load' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const whatsapp = useMemo(() => accounts?.whatsapp || [], [accounts]);
  const closedSessions = useMemo(() => whatsapp.filter(isClosedSession), [whatsapp]);
  const selectedClosed = useMemo(
    () => [...selected].filter((id) => closedSessions.some((row) => row.id === id)),
    [selected, closedSessions]
  );

  function toggleSelect(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAllClosed() {
    setSelected(new Set(closedSessions.map((row) => row.id)));
  }

  async function removeWhatsAppSession(row) {
    const closed = isClosedSession(row);
    if (
      !window.confirm(
        `Remove WhatsApp session #${row.id} (${row.status})?\n\nThis disconnects it and deletes its saved credentials and browser profile. Its filter jobs are NOT deleted — they stay with ${row.owner_email || 'the login account'} and keep working on the remaining devices.`
      )
    ) {
      return;
    }
    if (!closed) {
      // Double confirmation for a live session: almost never what you want.
      if (!window.confirm(`Session #${row.id} is CONNECTED. Really remove a live session?`)) return;
    }
    setRemovingSession(row.id);
    try {
      const { data } = await adminApi.deleteWhatsAppSession(row.id);
      toast.success(
        `Session #${row.id} removed` +
          (data?.detached_filters ? ` (${data.detached_filters} filter(s) kept)` : '')
      );
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not remove WhatsApp session'));
    } finally {
      setRemovingSession(null);
    }
  }

  async function openPreview(sessionIds) {
    setPreviewLoading(true);
    setPreview(null);
    setPreviewOpen(true);
    try {
      const { data } = await adminApi.cleanupClosedSessions({
        dry_run: true,
        limit: 500,
        ...(sessionIds?.length ? { session_ids: sessionIds } : {}),
      });
      setPreview({ ...data, sessionIds: sessionIds?.length ? sessionIds : null });
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not preview closed sessions'));
      setPreviewOpen(false);
    } finally {
      setPreviewLoading(false);
    }
  }

  async function runCleanup() {
    const ids = preview?.sessionIds || null;
    setCleaning(true);
    try {
      const { data } = await adminApi.cleanupClosedSessions({
        dry_run: false,
        limit: 500,
        ...(ids?.length ? { session_ids: ids } : {}),
      });
      toast.success(
        `Removed ${data.deleted} closed session(s)` +
          (data.preserved_filters ? `, kept ${data.preserved_filters} filter(s)` : '')
      );
      if (data.errors?.length) toast.error(data.errors[0]);
      setPreviewOpen(false);
      setPreview(null);
      await load();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not clean closed sessions'));
    } finally {
      setCleaning(false);
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
        <Metric label="Closed sessions" value={closedSessions.length} tone={closedSessions.length ? 'amber' : 'zinc'} detail="safe to remove" />
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
              <span className="font-medium text-zinc-400">closed</span>
              <span className="font-bold text-amber-200">{closedSessions.length}</span>
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
                <th className="py-2">Last updated</th>
              </tr>
            </thead>
            <tbody>
              {linkedin.map((row) => (
                <tr key={row.id} className="border-b border-surface-800/70">
                  <td className="py-3 pr-4 font-medium text-zinc-100">{row.linkedin_email}</td>
                  <td className="py-3 pr-4 text-xs text-zinc-400">{row.owner_email || '—'}</td>
                  <td className="py-3 pr-4 text-zinc-300">{row.label || '—'}</td>
                  <td className="py-3 pr-4">
                    <div className="flex items-center gap-2">
                      <span className="capitalize text-zinc-300">{row.status || '—'}</span>
                      {row.profile_missing &&
                        ['active', 'valid', 'pending_verification'].includes(row.status) && (
                          <ProfileMissingPill title="The stored browser profile is missing — the next session launch starts from a blank login. Check that the /app/profiles volume is mounted." />
                        )}
                    </div>
                  </td>
                  <td className="py-3 pr-4 text-xs text-zinc-500">{formatDate(row.created_at)}</td>
                  <td className="py-3 text-xs text-zinc-500">{formatDate(row.updated_at)}</td>
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
      <Section
        title="WhatsApp sessions"
        description="All WhatsApp sessions (newest first). Closed sessions can be removed — their filters stay with the login account."
        actions={
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={selectAllClosed}
              disabled={!closedSessions.length}
              className="btn-secondary px-3 py-1.5 text-xs disabled:opacity-50"
            >
              Select closed ({closedSessions.length})
            </button>
            {selected.size > 0 && (
              <button
                type="button"
                onClick={() => setSelected(new Set())}
                className="btn-secondary px-3 py-1.5 text-xs"
              >
                Clear ({selected.size})
              </button>
            )}
            <button
              type="button"
              onClick={() => openPreview(selectedClosed.length ? selectedClosed : null)}
              disabled={!closedSessions.length}
              className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-amber-500 disabled:opacity-50"
            >
              {selectedClosed.length ? `Review & remove ${selectedClosed.length}…` : 'Review & remove closed…'}
            </button>
          </div>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="text-xs uppercase tracking-wider text-zinc-500">
              <tr className="border-b border-surface-700">
                <th className="py-2 pr-4">
                  <input
                    type="checkbox"
                    aria-label="Select all closed sessions"
                    checked={closedSessions.length > 0 && selectedClosed.length === closedSessions.length}
                    onChange={(e) => (e.target.checked ? selectAllClosed() : setSelected(new Set()))}
                  />
                </th>
                <th className="py-2 pr-4">Session</th>
                <th className="py-2 pr-4">Owner</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Active</th>
                <th className="py-2 pr-4">Last updated</th>
                <th className="py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {whatsapp.map((row) => {
                const closed = isClosedSession(row);
                return (
                  <tr key={row.id} className="border-b border-surface-800/70">
                    <td className="py-3 pr-4">
                      <input
                        type="checkbox"
                        aria-label={`Select session ${row.id}`}
                        checked={selected.has(row.id)}
                        onChange={() => toggleSelect(row.id)}
                      />
                    </td>
                    <td className="py-3 pr-4 font-mono text-xs text-zinc-300">
                      session #{row.id}
                      {closed && (
                        <span className="ml-2 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-300 ring-1 ring-inset ring-amber-500/20">
                          closed
                        </span>
                      )}
                    </td>
                    <td className="py-3 pr-4 text-xs text-zinc-400">{row.owner_email || '—'}</td>
                    <td className="py-3 pr-4">
                      <div className="flex items-center gap-2 capitalize">
                        <span className="text-zinc-300">{row.status}</span>
                        {row.profile_missing && row.status === 'connected' && (
                          <ProfileMissingPill title="The shared WhatsApp browser profile is missing — the next scan/connect starts from a blank QR screen. Check that the /app/profiles volume is mounted." />
                        )}
                      </div>
                    </td>
                    <td className="py-3 pr-4">
                      <span className={row.is_active ? 'text-emerald-300' : 'text-zinc-500'}>
                        {row.is_active ? 'Yes' : 'No'}
                      </span>
                    </td>
                    <td className="py-3 pr-4 text-xs text-zinc-500">{formatDate(row.updated_at)}</td>
                    <td className="py-3">
                      <button
                        type="button"
                        onClick={() => removeWhatsAppSession(row)}
                        disabled={removingSession === row.id}
                        className="rounded-lg border border-red-500/20 bg-red-500/10 px-2.5 py-1.5 text-xs font-semibold text-red-300 transition hover:bg-red-500/20 disabled:opacity-50"
                      >
                        {removingSession === row.id ? 'Removing…' : 'Remove'}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!whatsapp.length && (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-zinc-500">No WhatsApp sessions yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs leading-5 text-zinc-500">
          Removing a session deletes its credentials and browser profile and revokes its queued scan
          tasks. Filter jobs are never deleted — they stay attached to the login account and keep
          working on that account&apos;s remaining devices.
        </p>
      </Section>

      {/* Bulk cleanup preview modal */}
      <Modal open={previewOpen} onClose={() => !cleaning && setPreviewOpen(false)} title="Remove closed sessions" wide>
        {previewLoading ? (
          <div className="flex h-32 items-center justify-center">
            <Spinner />
          </div>
        ) : preview ? (
          <div className="space-y-4">
            {preview.matched === 0 ? (
              <p className="text-sm text-zinc-400">No closed sessions match — nothing to remove.</p>
            ) : (
              <>
                <p className="text-sm text-zinc-300">
                  <span className="font-bold text-amber-200">{preview.matched}</span> closed{' '}
                  {preview.matched === 1 ? 'session' : 'sessions'} will be removed
                  {preview.sessionIds ? ' (from your selection)' : ''}:
                </p>
                <div className="max-h-64 overflow-auto rounded-lg border border-surface-700">
                  <table className="w-full text-left text-xs">
                    <thead className="sticky top-0 bg-surface-800 text-[11px] uppercase text-zinc-500">
                      <tr>
                        <th className="px-3 py-2">Session</th>
                        <th className="px-3 py-2">Owner</th>
                        <th className="px-3 py-2">Status</th>
                        <th className="px-3 py-2">Last updated</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-surface-700/50">
                      {preview.sessions.map((row) => (
                        <tr key={row.id}>
                          <td className="px-3 py-1.5 font-mono text-zinc-300">#{row.id}</td>
                          <td className="px-3 py-1.5 text-zinc-400">{row.owner_email || '—'}</td>
                          <td className="px-3 py-1.5 capitalize text-zinc-300">{row.status}</td>
                          <td className="px-3 py-1.5 text-zinc-500">{formatDate(row.updated_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!!preview.skipped?.length && (
                  <p className="text-xs text-zinc-500">
                    Skipped {preview.skipped.length} selected{' '}
                    {preview.skipped.length === 1 ? 'session' : 'sessions'} still connected/active.
                  </p>
                )}
                <p className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-200">
                  Filters are preserved: attached filter jobs are unlinked from the removed{' '}
                  {preview.matched === 1 ? 'device' : 'devices'} and stay with their login accounts.
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
                onClick={runCleanup}
                disabled={cleaning || preview.matched === 0}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-500 disabled:opacity-50"
              >
                {cleaning ? 'Removing…' : `Remove ${preview.matched}`}
              </button>
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
