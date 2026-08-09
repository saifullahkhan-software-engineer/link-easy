import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useAuth } from '../context/AuthContext';

const navItems = [
  {
    to: '/app/account',
    label: 'Account',
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
    end: true,   // exact match only — prevents double-highlight with /campaigns/create
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5 14.25 3l6.75 6.75-10.5 10.5H3.75v-6.75ZM12.75 6 18 11.25" />
      </svg>
    ),
  },
  {
    to: '/app/feed-scroll',
    label: 'Feed Scroll',
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 7.5h1.5m-1.5 3h1.5m-7.5 3h7.5m-7.5 3h7.5m3-9h3.375c.621 0 1.125.504 1.125 1.125V18a2.25 2.25 0 0 1-2.25 2.25M16.5 7.5V18a2.25 2.25 0 0 0 2.25 2.25M16.5 7.5V4.875c0-.621-.504-1.125-1.125-1.125H4.125C3.504 3.75 3 4.254 3 4.875V18a2.25 2.25 0 0 0 2.25 2.25h13.5M6 7.5h3v3H6v-3Z" />
      </svg>
    ),
  },
  {
    to: '/app/whatsapp-scanner',
    label: 'WhatsApp Scanner',
    icon: (
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 9.75a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375m-13.5 3.01c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 0 1 .778-.332 48.294 48.294 0 0 0 5.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z" />
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
    navigate('/', { replace: true });
  }

  function handleGoHome() {
    navigate('/', { replace: false });
  }

  return (
    <div className="flex min-h-screen bg-surface-950">
      {/* Sidebar */}
      <aside className="fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-surface-700 bg-surface-900">
        <Link to="/" className="flex h-16 items-center gap-2.5 border-b border-surface-700 px-5 transition hover:bg-surface-800/50">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="text-lg font-bold tracking-tight text-zinc-100">
            Link<span className="text-accent-400">Easy</span>
          </span>
        </Link>

        <nav className="flex-1 space-y-1 p-3">
          <p className="px-3 pb-2 pt-3 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
            Workspace
          </p>
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
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

        <div className="border-t border-surface-700 p-3 space-y-3">
          {/* User block */}
          <div className="flex items-center gap-3 rounded-lg bg-surface-800/60 px-2.5 py-2.5">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-500/15 text-sm font-bold text-accent-300">
              {(name || email || '?').slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              {name && <p className="truncate text-sm font-medium text-zinc-200">{name}</p>}
              <p className="truncate text-xs text-zinc-500">{email}</p>
            </div>
          </div>

          {/* Actions */}
          <div className="space-y-2">
            <button
              onClick={handleGoHome}
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
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0 0 13.5 3h-6a2.25 2.25 0 0 0-2.25 2.25v13.5A2.25 2.25 0 0 0 7.5 21h6a2.25 2.25 0 0 0 2.25-2.25V15m3-3-3-3m3 3-3 3m3-3H9" />
              </svg>
              Logout
            </button>
          </div>

          <p className="px-1 text-[11px] text-zinc-500">LinkEasy • v1.0</p>
        </div>
      </aside>

      {/* Main content */}
      <main className="ml-64 min-w-0 flex-1 p-8">
        <Outlet />
      </main>
    </div>
  );
}
