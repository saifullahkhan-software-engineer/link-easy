import { useEffect, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useAuth } from '../context/AuthContext';
import { useAdminAccess } from '../hooks/useAdminAccess';
import { useFeatures } from '../hooks/useFeatures';
import BetaBanner from './BetaBanner';
import HostedDemoBanner from './HostedDemoBanner';
import AssistantWidget from './assistant/AssistantWidget';
import { INBOX_CHANNELS } from '../constants/inbox';

/**
 * App module shell — the customer-facing product.
 *
 * Product groups (Social Scheduler, Gmail, Ultimate Inbox, LinkedIn, WhatsApp Scan) are collapsible
 * so the sidebar stays usable as items grow. The nav itself scrolls; the user
 * block stays pinned. On small screens the sidebar is a drawer.
 */

const accountItem = {
  to: '/app/account',
  label: 'Accounts',
  icon: (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.5 20.25a7.5 7.5 0 0 1 15 0" />
    </svg>
  ),
};

const linkedinGroup = {
  label: 'LinkedIn',
  icon: (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M20.45 20.45h-3.55v-5.57c0-1.33-.03-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.36V9h3.41v1.56h.05c.47-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.45v6.29ZM5.34 7.43a1.97 1.97 0 1 1 0-3.94 1.97 1.97 0 0 1 0 3.94ZM7.12 20.45H3.55V9h3.57v11.45ZM22.22 0H1.77C.79 0 0 .77 0 1.72v20.56C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.72V1.72C24 .77 23.2 0 22.22 0Z"
      />
    </svg>
  ),
  items: [
    {
      to: '/app/campaigns/create',
      label: 'Create Campaign',
    },
    {
      to: '/app/campaigns',
      label: 'Campaign Status',
      end: true,
    },
    {
      to: '/app/feed-scroll',
      label: 'Feed Scan',
    },
    {
      to: '/app/linkedin-live',
      label: 'LinkedIn Live Chat',
      needsLinkedIn: true,
    },
    {
      to: '/app/linkedin-profile',
      label: 'Profile Scan (PDF)',
      needsLinkedIn: true,
    },
  ],
};

const whatsappScanGroup = {
  label: 'WhatsApp Scan',
  icon: (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M8.625 9.75a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375m-13.5 3.01c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 0 1 .778-.332 48.294 48.294 0 0 0 5.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z"
      />
    </svg>
  ),
  items: [
    { to: '/app/whatsapp-scanner', label: 'WhatsApp Group Scan' },
  ],
};

const gmailGroup = {
  label: 'Gmail',
  icon: (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
      <path d="M1.5 5.25A2.25 2.25 0 0 1 3.75 3h16.5a2.25 2.25 0 0 1 2.25 2.25v13.5A2.25 2.25 0 0 1 20.25 21H3.75a2.25 2.25 0 0 1-2.25-2.25V5.25Zm1.5.66v12.84c0 .41.34.75.75.75h16.5c.41 0 .75-.34.75-.75V5.91l-8.28 6.07a1.5 1.5 0 0 1-1.68 0L3 5.91Zm1.03-.66L12 11.32l7.97-6.07H4.03Z" />
    </svg>
  ),
  items: [
    { to: '/app/gmail', label: 'Inbox', end: true },
    { to: '/app/gmail/compose', label: 'Compose' },
  ],
};

const socialGroup = {
  label: 'Social Scheduler',
  icon: (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="m15.75 10.5 4.72-4.72a.75.75 0 0 1 1.28.53v11.38a.75.75 0 0 1-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 0 0 2.25-2.25v-9a2.25 2.25 0 0 0-2.25-2.25h-9A2.25 2.25 0 0 0 2.25 7.5v9a2.25 2.25 0 0 0 2.25 2.25Z"
      />
    </svg>
  ),
  items: [
    { to: '/app/social-scheduler', label: 'Overview', end: true },
    { to: '/app/social-scheduler/schedule', label: 'Upload Shorts' },
    { to: '/app/social-scheduler/posts', label: 'Upload Posts' },
    { to: '/app/social-scheduler/queue', label: 'Queue' },
    { to: '/app/social-scheduler/calendar', label: 'Calendar' },
    { to: '/app/social-scheduler/history', label: 'History' },
  ],
};

const inboxGroup = {
  label: 'Ultimate Inbox',
  icon: (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M4 4h16l2 10v6H2v-6L4 4ZM2 14h6l2 3h4l2-3h6" />
    </svg>
  ),
  items: INBOX_CHANNELS,
};

const PRODUCT_GROUPS = [socialGroup, gmailGroup, inboxGroup, linkedinGroup, whatsappScanGroup];

function pathMatches(item, pathname) {
  if (item.end) return pathname === item.to;
  return pathname === item.to || pathname.startsWith(`${item.to}/`);
}

function groupContainsPath(group, pathname) {
  return group.items.some((item) => pathMatches(item, pathname));
}

function Chevron({ open }) {
  return (
    <svg
      className={`h-4 w-4 shrink-0 text-zinc-500 transition-transform ${open ? 'rotate-90' : ''}`}
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden="true"
    >
      <path
        fillRule="evenodd"
        d="M7.21 14.77a.75.75 0 0 1 .02-1.06L11.168 10 7.23 6.29a.75.75 0 1 1 1.04-1.08l4.5 4.25a.75.75 0 0 1 0 1.08l-4.5 4.25a.75.75 0 0 1-1.06-.02Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function NavGroup({ group, pathname, linkedinEnabled, onNavigate }) {
  const active = groupContainsPath(group, pathname);
  const [open, setOpen] = useState(active);
  const groupId = `nav-${group.label.toLowerCase().replaceAll(' ', '-')}`;

  useEffect(() => {
    if (active) setOpen(true);
  }, [active]);

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={groupId}
        className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider transition ${
          active
            ? 'bg-surface-800/80 text-zinc-200'
            : 'text-zinc-500 hover:bg-surface-800 hover:text-zinc-300'
        }`}
      >
        <span className={active ? 'text-zinc-300' : 'text-zinc-600'}>{group.icon}</span>
        <span className="min-w-0 flex-1 truncate">{group.label}</span>
        <Chevron open={open} />
      </button>
      {open && (
        <div id={groupId} className="mt-1 space-y-0.5" role="group" aria-label={`${group.label} pages`}>
          {group.items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              title={item.comingSoon ? `${item.label} — Coming soon` : item.label}
              onClick={onNavigate}
              className={({ isActive }) =>
                `flex items-center gap-2 rounded-lg py-2 pl-6 pr-3 text-sm transition ${
                  isActive
                    ? 'bg-accent-500/10 font-medium text-accent-300 ring-1 ring-inset ring-accent-500/20'
                    : 'text-zinc-400 hover:bg-surface-800 hover:text-zinc-100'
                }`
              }
            >
              <span className="shrink-0 text-zinc-600" aria-hidden="true">•</span>
              <span className="min-w-0 flex-1 leading-5">{item.label}</span>
              {item.comingSoon && (
                <span className="shrink-0 rounded bg-surface-700 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-zinc-400">Soon</span>
              )}
              {item.needsLinkedIn && !linkedinEnabled && (
                <span
                  title="Paused — needs proxy setup"
                  className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-amber-300"
                >
                  Paused
                </span>
              )}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  );
}

function MenuIcon({ open }) {
  return open ? (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
    </svg>
  ) : (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5M3.75 17.25h16.5" />
    </svg>
  );
}

export default function AppLayout() {
  const { email, name, logout } = useAuth();
  const { canSeeAdmin } = useAdminAccess();
  const { linkedinEnabled } = useFeatures();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!mobileOpen) return undefined;
    const onKey = (e) => e.key === 'Escape' && setMobileOpen(false);
    window.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [mobileOpen]);

  function handleLogout() {
    logout();
    toast.success('Signed out');
    navigate('/', { replace: true });
  }

  const closeMobile = () => setMobileOpen(false);

  const sidebar = (
    <>
      <Link
        to="/"
        onClick={closeMobile}
        className="flex h-14 shrink-0 items-center gap-2.5 border-b border-surface-700 px-4 transition hover:bg-surface-800/50 sm:h-16 sm:px-5"
      >
        <img src="/favicon.svg" alt="" className="h-7 w-7" />
        <span className="text-lg font-bold tracking-tight text-zinc-100">
          Link<span className="text-accent-400">Easy</span>
        </span>
        <span className="rounded-md bg-accent-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-accent-300">
          App
        </span>
      </Link>

      <nav aria-label="Workspace navigation" className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain p-3 scrollbar-thin">
        <div>
          <p className="px-3 pb-2 pt-1 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
            Workspace
          </p>
          <NavLink
            to={accountItem.to}
            onClick={closeMobile}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                isActive
                  ? 'bg-accent-500/10 text-accent-300 ring-1 ring-inset ring-accent-500/20'
                  : 'text-zinc-300 hover:bg-surface-800 hover:text-zinc-100'
              }`
            }
          >
            {accountItem.icon}
            {accountItem.label}
          </NavLink>
        </div>

        {PRODUCT_GROUPS.map((group) => (
          <NavGroup
            key={group.label}
            group={group}
            pathname={pathname}
            linkedinEnabled={linkedinEnabled}
            onNavigate={closeMobile}
          />
        ))}
      </nav>

      <div className="shrink-0 space-y-3 border-t border-surface-700 p-3">
        <div className="flex items-center gap-3 rounded-lg bg-surface-800/60 px-2.5 py-2.5">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-500/15 text-sm font-bold text-accent-300">
            {(name || email || '?').slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            {name && <p className="truncate text-sm font-medium text-zinc-200">{name}</p>}
            <p className="truncate text-xs text-zinc-500">{email}</p>
          </div>
        </div>

        <div className="space-y-2">
          {canSeeAdmin && (
            <Link
              to="/admin"
              onClick={closeMobile}
              className="flex w-full items-center gap-2.5 rounded-lg border border-surface-700 bg-surface-800 px-3 py-2.5 text-sm font-medium text-zinc-300 transition hover:bg-surface-700 hover:text-zinc-100"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 3.5l7 3v5c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9v-5l7-3z" />
              </svg>
              Admin dashboard
            </Link>
          )}

          <button
            onClick={() => {
              closeMobile();
              navigate('/', { replace: false });
            }}
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

        <p className="px-1 text-[11px] text-zinc-500">LinkEasy App • v1.0</p>
      </div>
    </>
  );

  return (
    <div className="flex min-h-screen bg-surface-950">
      {mobileOpen && (
        <button
          type="button"
          aria-label="Close menu"
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={closeMobile}
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col border-r border-surface-700 bg-surface-900 transition-transform duration-200 ease-out lg:w-64 lg:translate-x-0 ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {sidebar}
      </aside>

      <div className="flex min-h-screen min-w-0 flex-1 flex-col lg:ml-64">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-surface-700 bg-surface-900/95 px-4 backdrop-blur lg:hidden">
          <button
            type="button"
            onClick={() => setMobileOpen((v) => !v)}
            className="rounded-lg p-2 text-zinc-300 hover:bg-surface-800 hover:text-zinc-100"
            aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={mobileOpen}
          >
            <MenuIcon open={mobileOpen} />
          </button>
          <Link to="/" className="flex min-w-0 items-center gap-2">
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            <span className="truncate text-base font-bold tracking-tight text-zinc-100">
              Link<span className="text-accent-400">Easy</span>
            </span>
          </Link>
        </header>

        <main className="min-w-0 flex-1 overflow-x-hidden p-4 sm:p-6 lg:p-8">
          <HostedDemoBanner />
          <BetaBanner />
          <Outlet />
        </main>
      </div>

      {/* The floating AI assistant is available throughout the app shell. */}
      <AssistantWidget />
    </div>
  );
}
