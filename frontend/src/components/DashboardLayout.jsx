import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useAuth } from '../context/AuthContext';

const navItems = [
  {
    to: '/app/account',
    label: 'LinkedIn Account',
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.5 20.25a7.5 7.5 0 0 1 15 0" />
      </svg>
    ),
  },
  {
    to: '/app/campaigns/create',
    label: 'Create Campaign',
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v6m3-3H9m12 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
      </svg>
    ),
  },
  {
    to: '/app/campaigns',
    label: 'Campaign Status',
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5 14.25 3l6.75 6.75-10.5 10.5H3.75v-6.75ZM12.75 6 18 11.25" />
      </svg>
    ),
  },
];

export default function DashboardLayout() {
  const { email, name, logout } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    toast.success('Signed out');
    navigate('/login', { replace: true });
  }

  return (
    <div className="flex min-h-screen bg-surface-950">
      {/* Sidebar */}
      <aside className="fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-surface-700 bg-surface-900">
        <div className="flex h-16 items-center gap-2.5 border-b border-surface-700 px-5">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="text-lg font-bold tracking-tight text-zinc-100">
            Reach<span className="text-accent-400">Pilot</span>
          </span>
        </div>

        <nav className="flex-1 space-y-1 p-3">
          <p className="px-3 pb-2 pt-3 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
            Workspace
          </p>
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                  isActive
                    ? 'bg-accent-500/10 text-accent-300 ring-1 ring-inset ring-accent-500/20'
                    : 'text-zinc-400 hover:bg-surface-800 hover:text-zinc-100'
                }`
              }
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-surface-700 p-3">
          <div className="flex items-center gap-3 rounded-lg px-2 py-2">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-500/15 text-sm font-bold text-accent-300">
              {(name || email || '?').slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              {name && <p className="truncate text-sm font-medium text-zinc-200">{name}</p>}
              <p className="truncate text-xs text-zinc-500">{email}</p>
            </div>
            <button
              onClick={handleLogout}
              title="Sign out"
              className="rounded-md p-1.5 text-zinc-500 transition hover:bg-surface-700 hover:text-red-300"
            >
              <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0 0 13.5 3h-6a2.25 2.25 0 0 0-2.25 2.25v13.5A2.25 2.25 0 0 0 7.5 21h6a2.25 2.25 0 0 0 2.25-2.25V15m3-3-3-3m3 3-3 3m3-3H9" />
              </svg>
            </button>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="ml-64 min-w-0 flex-1 p-8">
        <Outlet />
      </main>
    </div>
  );
}
