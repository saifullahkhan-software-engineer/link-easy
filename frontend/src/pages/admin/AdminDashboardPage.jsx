import { useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { adminApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../../components/Spinner';

function Metric({ label, value, detail, tone = 'zinc' }) {
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

function Section({ title, description, children, actions }) {
  return (
    <section className="rounded-2xl border border-surface-700 bg-surface-900/60 p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-zinc-100">{title}</h2>
          {description ? <p className="mt-1 text-sm text-zinc-500">{description}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

function StatusPills({ map }) {
  const entries = Object.entries(map || {});
  if (!entries.length) return <p className="text-sm text-zinc-500">None yet.</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="inline-flex items-center gap-2 rounded-lg border border-surface-700 bg-surface-800 px-3 py-1.5 text-xs text-zinc-300"
        >
          <span className="font-medium text-zinc-400">{key}</span>
          <span className="font-bold text-zinc-100">{value}</span>
        </span>
      ))}
    </div>
  );
}

const ALL_ROLES = ['admin', 'customer'];

export default function AdminDashboardPage() {
  const [overview, setOverview] = useState(null);
  const [users, setUsers] = useState([]);
  const [settings, setSettings] = useState([]);
  const [rateLimits, setRateLimits] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [savingEmail, setSavingEmail] = useState(null);
  const [draft, setDraft] = useState({});
  const [savingSettings, setSavingSettings] = useState(false);

  const load = useCallback(async (opts = {}) => {
    try {
      const [ov, us, st, rl] = await Promise.all([
        adminApi.overview(),
        adminApi.listUsers({ q: opts.q }),
        adminApi.getSettings(),
        adminApi.rateLimits(),
      ]);
      setOverview(ov.data);
      setUsers(us.data?.users || []);
      setSettings(st.data?.settings || []);
      setRateLimits(rl.data?.counters || []);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load the admin dashboard'), { id: 'admin-load' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const settingsByCategory = useMemo(() => {
    const grouped = {};
    settings.forEach((s) => {
      (grouped[s.category] = grouped[s.category] || []).push(s);
    });
    return grouped;
  }, [settings]);

  const toggleRole = async (user, role) => {
    const has = user.roles.includes(role);
    const next = has ? user.roles.filter((r) => r !== role) : [...user.roles, role];
    if (!next.length) {
      toast.error('A user needs at least one role');
      return;
    }
    setSavingEmail(user.email);
    try {
      const { data } = await adminApi.setUserRoles(user.email, next);
      setUsers((prev) =>
        prev.map((u) =>
          u.email === user.email ? { ...u, roles: data.roles, primary_role: data.primary_role } : u
        )
      );
      toast.success(`${user.email}: ${data.roles.join(', ')}`);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not update roles'));
    } finally {
      setSavingEmail(null);
    }
  };

  const saveSettings = async () => {
    const values = {};
    Object.entries(draft).forEach(([key, raw]) => {
      const spec = settings.find((s) => s.key === key);
      if (!spec) return;
      const parsed = spec.value_type === 'float' ? parseFloat(raw) : parseInt(raw, 10);
      if (!Number.isNaN(parsed)) values[key] = parsed;
    });
    if (!Object.keys(values).length) {
      toast('Nothing changed');
      return;
    }
    setSavingSettings(true);
    try {
      const { data } = await adminApi.updateSettings(values);
      setSettings(data?.settings || []);
      setDraft({});
      toast.success('Settings saved');
    } catch (err) {
      // The backend rejects anything above a safe cap with a readable message.
      toast.error(getErrorMessage(err, 'Could not save settings'));
    } finally {
      setSavingSettings(false);
    }
  };

  if (loading && !overview) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const u = overview?.users || {};
  const a = overview?.accounts || {};
  const j = overview?.jobs || {};
  const rl = overview?.rate_limits || {};

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent-400">Administration</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-zinc-50">Admin Dashboard</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
            Users, accounts, jobs, campaign parameters, and database-backed rate limits.
          </p>
        </div>
        <button type="button" onClick={() => load({ q: query })} className="btn-secondary px-4 py-2 text-sm">
          Refresh
        </button>
      </div>

      {/* ── Users / accounts / jobs ─────────────────────────────────────── */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Users" value={u.total ?? 0} detail={`${u.verified ?? 0} verified · ${u.admins ?? 0} admin`} />
        <Metric label="LinkedIn accounts" value={a.linkedin_total ?? 0} tone="indigo" detail={`${a.whatsapp_total ?? 0} WhatsApp sessions`} />
        <Metric label="Jobs (total)" value={j.total ?? 0} tone="emerald" detail={`${j.last_24h ?? 0} in the last 24h`} />
        <Metric label="Campaigns" value={j.campaigns_total ?? 0} tone="amber" detail={`${rl.active_windows_last_hour ?? 0} rate-limit windows active`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Jobs by status" description="Every campaign job currently recorded.">
          <StatusPills map={j.by_status} />
        </Section>
        <Section title="Accounts by status" description="LinkedIn account health.">
          <StatusPills map={a.linkedin_by_status} />
        </Section>
      </div>

      {/* ── Users and roles ─────────────────────────────────────────────── */}
      <Section
        title="Users and roles"
        description="Assign roles per user. A user with both roles sees both dashboards."
        actions={
          <form
            onSubmit={(e) => {
              e.preventDefault();
              load({ q: query });
            }}
            className="flex gap-2"
          >
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search email or name"
              className="rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600"
            />
            <button type="submit" className="btn-secondary px-3 py-2 text-sm">Search</button>
          </form>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="text-xs uppercase tracking-wider text-zinc-500">
              <tr className="border-b border-surface-700">
                <th className="py-2 pr-4">User</th>
                <th className="py-2 pr-4">Verified</th>
                <th className="py-2 pr-4">Accounts</th>
                <th className="py-2 pr-4">Campaigns</th>
                <th className="py-2">Roles</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.email} className="border-b border-surface-800/70">
                  <td className="py-3 pr-4">
                    <div className="font-medium text-zinc-100">{user.first_name} {user.last_name}</div>
                    <div className="text-xs text-zinc-500">{user.email}</div>
                  </td>
                  <td className="py-3 pr-4">
                    <span className={user.is_verified ? 'text-emerald-300' : 'text-amber-300'}>
                      {user.is_verified ? 'Yes' : 'No'}
                    </span>
                  </td>
                  <td className="py-3 pr-4 text-zinc-300">{user.linkedin_accounts}</td>
                  <td className="py-3 pr-4 text-zinc-300">{user.campaigns}</td>
                  <td className="py-3">
                    <div className="flex flex-wrap gap-2">
                      {ALL_ROLES.map((role) => {
                        const active = user.roles.includes(role);
                        return (
                          <button
                            key={role}
                            type="button"
                            disabled={savingEmail === user.email}
                            onClick={() => toggleRole(user, role)}
                            data-testid={`role-toggle-${user.email}-${role}`}
                            className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition disabled:opacity-50 ${
                              active
                                ? 'border-accent-500/40 bg-accent-500/10 text-accent-200'
                                : 'border-surface-700 bg-surface-900 text-zinc-500 hover:text-zinc-300'
                            }`}
                          >
                            {role}
                          </button>
                        );
                      })}
                    </div>
                  </td>
                </tr>
              ))}
              {!users.length && (
                <tr><td colSpan={5} className="py-6 text-center text-zinc-500">No users found.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>

      {/* ── Campaign parameters and job limits ──────────────────────────── */}
      <Section
        title="Campaign parameters, jobs and limits"
        description="Values are clamped to safe maximums to protect connected accounts."
        actions={
          <button
            type="button"
            onClick={saveSettings}
            disabled={savingSettings || !Object.keys(draft).length}
            className="btn-primary px-4 py-2 text-sm disabled:opacity-50"
            data-testid="save-settings"
          >
            {savingSettings ? 'Saving…' : 'Save changes'}
          </button>
        }
      >
        <div className="space-y-6">
          {Object.entries(settingsByCategory).map(([category, rows]) => (
            <div key={category}>
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500">{category}</h3>
              <div className="grid gap-3 md:grid-cols-2">
                {rows.map((s) => (
                  <label key={s.key} className="rounded-xl border border-surface-700 bg-surface-900 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium text-zinc-200">{s.key.split('.').slice(1).join('.')}</span>
                      <input
                        type="number"
                        step={s.value_type === 'float' ? '0.5' : '1'}
                        min={s.minimum ?? undefined}
                        max={s.maximum ?? undefined}
                        value={draft[s.key] ?? s.value}
                        onChange={(e) => setDraft((d) => ({ ...d, [s.key]: e.target.value }))}
                        className="w-28 rounded-lg border border-surface-700 bg-surface-950 px-2 py-1.5 text-right text-sm text-zinc-100"
                      />
                    </div>
                    <p className="mt-1.5 text-xs leading-5 text-zinc-500">{s.description}</p>
                    {s.maximum != null && (
                      <p className="mt-0.5 text-[11px] text-zinc-600">max {s.maximum} · default {s.default}</p>
                    )}
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Section>

      {/* ── Rate limits ─────────────────────────────────────────────────── */}
      <Section
        title="Rate limits (database-backed)"
        description="Live counters. Limits are enforced in PostgreSQL, so Redis stays free for jobs."
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-xs uppercase tracking-wider text-zinc-500">
              <tr className="border-b border-surface-700">
                <th className="py-2 pr-4">Identity</th>
                <th className="py-2 pr-4">Bucket</th>
                <th className="py-2 pr-4">Used</th>
                <th className="py-2 pr-4">Window started</th>
                <th className="py-2">Action</th>
              </tr>
            </thead>
            <tbody>
              {rateLimits.map((c) => (
                <tr key={`${c.identity}-${c.bucket}-${c.window_started_at}`} className="border-b border-surface-800/70">
                  <td className="py-2.5 pr-4 font-mono text-xs text-zinc-300">{c.identity}</td>
                  <td className="py-2.5 pr-4 text-zinc-300">{c.bucket}</td>
                  <td className="py-2.5 pr-4 font-semibold text-zinc-100">{c.request_count}</td>
                  <td className="py-2.5 pr-4 text-xs text-zinc-500">
                    {c.window_started_at ? new Date(c.window_started_at).toLocaleString() : '—'}
                  </td>
                  <td className="py-2.5">
                    <button
                      type="button"
                      className="rounded-lg border border-surface-700 px-2.5 py-1 text-xs text-zinc-400 hover:text-zinc-100"
                      onClick={async () => {
                        try {
                          await adminApi.resetRateLimit({ identity: c.identity, bucket: c.bucket });
                          toast.success('Counter reset');
                          load({ q: query });
                        } catch (err) {
                          toast.error(getErrorMessage(err, 'Could not reset'));
                        }
                      }}
                    >
                      Reset
                    </button>
                  </td>
                </tr>
              ))}
              {!rateLimits.length && (
                <tr><td colSpan={5} className="py-6 text-center text-zinc-500">No active counters.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
