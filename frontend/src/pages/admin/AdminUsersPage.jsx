import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { adminApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../../components/Spinner';
import { Section } from '../../components/admin/shared';

const ALL_ROLES = ['admin', 'customer'];

/**
 * Admin: Users.
 *
 * Users and their roles — the /admin/users module of the admin sidebar.
 */
export default function AdminUsersPage() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [savingEmail, setSavingEmail] = useState(null);

  const load = useCallback(async (opts = {}) => {
    try {
      const { data } = await adminApi.listUsers({ q: opts.q });
      setUsers(data?.users || []);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load users'), { id: 'admin-users-load' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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

  if (loading && !users.length) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-amber-400">Administration</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-zinc-50">Users</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
            Assign roles per user. A user with both roles sees both dashboards.
          </p>
        </div>
        <button type="button" onClick={() => load({ q: query })} className="btn-secondary px-4 py-2 text-sm">
          Refresh
        </button>
      </div>

      <Section
        title="Users and roles"
        description="Search by email or name, then toggle the roles a user holds."
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
    </div>
  );
}
