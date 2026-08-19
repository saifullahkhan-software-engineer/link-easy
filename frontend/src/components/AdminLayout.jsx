import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useAuth } from '../context/AuthContext';

/**
 * Admin module shell — a dashboard completely separate from the app.
 *
 * Its sidebar contains the four admin areas:
 *   1. Accounts            — LinkedIn accounts + WhatsApp sessions
 *   2. Users               — users and roles
 *   3. LinkedIn            — LinkedIn jobs and campaign parameters
 *   4. WhatsApp            — WhatsApp jobs and parameters
 *
 * This lives under /admin, guarded by AdminRoute, and shares nothing with the
 * app's sidebar (/app) or the operations dashboard (/dashboard).
 */

const adminNav = [
  {
    to: '/admin/accounts',
    label: 'Accounts',
    sub: 'LinkedIn + WhatsApp sessions',
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.5 20.25a7.5 7.5 0 0 1 15 0" />
      </svg>
    ),
  },
  {
    to: '/admin/users',
    label: 'Users',
    sub: 'Users and roles',
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
      </svg>
    ),
  },
  {
    to: '/admin/linkedin',
    label: 'LinkedIn',
    sub: 'Jobs & campaign parameters',
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M20.45 20.45h-3.55v-5.57c0-1.33-.03-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.36V9h3.41v1.56h.05c.47-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.45v6.29ZM5.34 7.43a1.97 1.97 0 1 1 0-3.94 1.97 1.97 0 0 1 0 3.94ZM7.12 20.45H3.55V9h3.57v11.45ZM22.22 0H1.77C.79 0 0 .77 0 1.72v20.56C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.72V1.72C24 .77 23.2 0 22.22 0Z"
        />
      </svg>
    ),
  },
  {
    to: '/admin/whatsapp',
    label: 'WhatsApp',
    sub: 'Jobs & parameters',
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M8.625 9.75a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375m-13.5 3.01c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 0 1 .778-.332 48.294 48.294 0 0 0 5.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z"
        />
      </svg>
    ),
  },
];

export default function AdminLayout() {
  const { email, name, logout } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    toast.success('Signed out');
    navigate('/', { replace: true });
  }

  return (
    <div className="flex min-h-screen bg-surface-950">
      {/* Sidebar — admin module only. */}
      <aside className="fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-surface-700 bg-surface-900">
        <Link to="/admin" className="flex h-16 items-center gap-2.5 border-b border-surface-700 px-5 transition hover:bg-surface-800/50">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="text-lg font-bold tracking-tight text-zinc-100">
            Link<span className="text-accent-400">Easy</span>
          </span>
          <span className="rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-300">
            Admin
          </span>
        </Link>

        <nav className="flex-1 space-y-1 overflow-y-auto p-3">
          <p className="px-3 pb-2 pt-1 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
            Admin dashboard
          </p>
          {adminNav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-start gap-3 rounded-lg px-3 py-2.5 transition ${
                  isActive
                    ? 'bg-amber-500/10 text-amber-200 ring-1 ring-inset ring-amber-500/20'
                    : 'text-zinc-300 hover:bg-surface-800 hover:text-zinc-100'
                }`
              }
            >
              <span className="mt-0.5 shrink-0 text-zinc-500">{item.icon}</span>
              <span className="min-w-0">
                <span className="block text-sm font-semibold">{item.label}</span>
                <span className="block truncate text-[11px] text-zinc-500">{item.sub}</span>
              </span>
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-surface-700 p-3 space-y-3">
          {/* User block */}
          <div className="flex items-center gap-3 rounded-lg bg-surface-800/60 px-2.5 py-2.5">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-500/15 text-sm font-bold text-amber-300">
              {(name || email || '?').slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              {name && <p className="truncate text-sm font-medium text-zinc-200">{name}</p>}
              <p className="truncate text-xs text-zinc-500">{email}</p>
            </div>
          </div>

          <div className="space-y-2">
            <Link
              to="/app/account"
              className="flex w-full items-center gap-2.5 rounded-lg border border-surface-700 bg-surface-800 px-3 py-2.5 text-sm font-medium text-zinc-300 transition hover:bg-surface-700 hover:text-zinc-100"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.625A1.875 1.875 0 0 0 3.75 7.875v10.5c0 .621.504 1.125 1.125 1.125h10.5a1.875 1.875 0 0 0 1.875-1.875V10.5m-9.75 3 9-9m0 0h-5.25m5.25 0v5.25" />
              </svg>
              Open App
            </Link>
            <button
              onClick={() => navigate('/', { replace: false })}
              className="flex w-full items-center gap-2.5 rounded-lg border border-surface-700 bg-surface-800 px-3 py-2.5 text-sm font-medium text-zinc-300 transition hover:bg-surface-700 hover:text-zinc-100"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12 11.25 3 20.25 12m-1.5 1.5V21a.75.75 0 0 1-.75.75h-4.5A.75.75 0 0 1 12.75 21v-4.5a.75.75 0 0 0-.75-.75h-1.5a.75.75 0 0 0-.75.75V21A.75.75 0 0 1 9 21.75h-4.5A.75.75 0 0 1 3.75 21v-7.5L2.25 12Z" />
              </svg>
              Back to Main Site
            </button>
            <button
              onClick={handleLogout}
              className="flex w-full items-center gap-2.5 rounded-lg bg-red-500/10 px-3 py-2.5 text-sm font-medium text-red-300 ring-1 ring-inset ring-red-500/15 transition hover:bg-red-500/15 hover:text-red-200"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0 0 13.5 3h-6a2.25 2.25 0 0 0-2.25 2.25v13.5A2.25 2.25 0 0 0 7.5 21h6a2.25 2.25 0 0 0 2.25-2.25V15m3-3-3-3m3 3-3-3m3 3H9" />
              </svg>
              Logout
            </button>
          </div>

          <p className="px-1 text-[11px] text-zinc-500">LinkEasy Admin • v1.0</p>
        </div>
      </aside>

      {/* Main content */}
      <main className="ml-72 min-w-0 flex-1 p-8">
        <Outlet />
      </main>
    </div>
  );
}
