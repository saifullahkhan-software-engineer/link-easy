import { Link } from 'react-router-dom';
import { PLATFORMS, PLATFORM_LABELS } from '../../api/socialScheduler';

/* ───────────────────────── platform visuals ───────────────────────── */

const PLATFORM_STYLES = {
  youtube: 'text-red-300 bg-red-500/10 ring-red-500/30',
  instagram: 'text-pink-300 bg-pink-500/10 ring-pink-500/30',
  tiktok: 'text-cyan-300 bg-cyan-500/10 ring-cyan-500/30',
};

const PLATFORM_ICONS = {
  youtube: (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.5 3.6 12 3.6 12 3.6s-7.5 0-9.4.5A3 3 0 0 0 .5 6.2 31 31 0 0 0 0 12a31 31 0 0 0 .5 5.8 3 3 0 0 0 2.1 2.1c1.9.5 9.4.5 9.4.5s7.5 0 9.4-.5a3 3 0 0 0 2.1-2.1A31 31 0 0 0 24 12a31 31 0 0 0-.5-5.8ZM9.6 15.6V8.4l6.2 3.6-6.2 3.6Z" />
    </svg>
  ),
  instagram: (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="5" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="17.5" cy="6.5" r="1" fill="currentColor" stroke="none" />
    </svg>
  ),
  tiktok: (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M16.6 5.8A4.3 4.3 0 0 1 15.5 3h-3.1v12.4a2.6 2.6 0 1 1-2.6-2.6c.3 0 .5 0 .8.1V9.7a5.7 5.7 0 1 0 4.9 5.7V9.1a7.3 7.3 0 0 0 4.3 1.4V7.4a4.3 4.3 0 0 1-3.2-1.6Z" />
    </svg>
  ),
};

/** Small pill for one platform (optionally with its icon). */
export function PlatformChip({ platform, withLabel = true, className = '' }) {
  const meta = PLATFORMS.find((p) => p.id === platform);
  return (
    <span
      title={PLATFORM_LABELS[platform] || platform}
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${
        PLATFORM_STYLES[platform] || 'text-zinc-300 bg-zinc-500/10 ring-zinc-500/30'
      } ${className}`}
    >
      {PLATFORM_ICONS[platform] || null}
      {withLabel ? meta?.short || platform : null}
    </span>
  );
}

export function PlatformIcon({ platform, className = 'h-5 w-5' }) {
  return (
    <span className={`inline-flex items-center justify-center ${className}`}>
      {PLATFORM_ICONS[platform] || <span>•</span>}
    </span>
  );
}

/* ───────────────────────── status badges ───────────────────────── */

const base =
  'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap';

const POST_STATUS = {
  pending: { label: 'Scheduled', cls: 'text-amber-300 bg-amber-500/10 ring-amber-500/30', dot: 'bg-amber-400' },
  posting: { label: 'Publishing…', cls: 'text-indigo-300 bg-indigo-500/10 ring-indigo-500/30', dot: 'bg-indigo-400 animate-pulse' },
  posted: { label: 'Published', cls: 'text-emerald-300 bg-emerald-500/10 ring-emerald-500/30', dot: 'bg-emerald-400' },
  failed: { label: 'Failed', cls: 'text-red-300 bg-red-500/10 ring-red-500/30', dot: 'bg-red-400' },
  cancelled: { label: 'Cancelled', cls: 'text-zinc-300 bg-zinc-500/10 ring-zinc-500/30', dot: 'bg-zinc-400' },
};

export function PostStatusBadge({ status }) {
  const s = POST_STATUS[status] || {
    label: status || 'Unknown',
    cls: 'text-zinc-300 bg-zinc-500/10 ring-zinc-500/30',
    dot: 'bg-zinc-400',
  };
  return (
    <span className={`${base} ${s.cls}`} data-status={status}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

/* ───────────────────────── dates ───────────────────────── */

export function formatDateTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function formatTime(value) {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

/** "in 2 days", "3 hours ago" — no date library needed. */
export function formatRelative(value, now = Date.now()) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  const diff = d.getTime() - now;
  const abs = Math.abs(diff);
  const units = [
    ['day', 86_400_000],
    ['hour', 3_600_000],
    ['minute', 60_000],
  ];
  for (const [name, ms] of units) {
    if (abs >= ms) {
      const n = Math.floor(abs / ms);
      const word = `${n} ${name}${n === 1 ? '' : 's'}`;
      return diff >= 0 ? `in ${word}` : `${word} ago`;
    }
  }
  return diff >= 0 ? 'in under a minute' : 'just now';
}

/** Value for <input type="datetime-local"> from an ISO string (local time). */
export function toLocalInputValue(value) {
  const d = value ? new Date(value) : new Date(Date.now() + 60 * 60 * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** ISO (UTC) string from a datetime-local input value. */
export function fromLocalInputValue(value) {
  return value ? new Date(value).toISOString() : null;
}

/* ───────────────────────── layout helpers ───────────────────────── */

const TABS = [
  { to: '/app/social-scheduler', label: 'Overview', end: true },
  { to: '/app/social-scheduler/schedule', label: 'Schedule' },
  { to: '/app/social-scheduler/queue', label: 'Queue' },
  { to: '/app/social-scheduler/calendar', label: 'Calendar' },
  { to: '/app/social-scheduler/history', label: 'History' },
  { to: '/app/social-scheduler/settings', label: 'Settings' },
];

/** Page header shared by every social-scheduler page. */
export function SocialPageHeader({ title, description, action, current }) {
  return (
    <div className="mb-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-accent-400">Social Scheduler</p>
          <h1 className="mt-1 text-2xl font-bold text-zinc-100">{title}</h1>
          {description && <p className="mt-1 text-sm text-zinc-400">{description}</p>}
        </div>
        {action}
      </div>
      <nav className="mt-5 flex flex-wrap gap-1 border-b border-surface-700" aria-label="Social scheduler sections">
        {TABS.map((tab) => {
          const active = tab.to === current;
          return (
            <Link
              key={tab.to}
              to={tab.to}
              aria-current={active ? 'page' : undefined}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition ${
                active
                  ? 'border-accent-400 text-accent-300'
                  : 'border-transparent text-zinc-400 hover:border-surface-600 hover:text-zinc-200'
              }`}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}

export function EmptyState({ icon = '📭', title, description, action }) {
  return (
    <div className="rounded-xl border border-surface-700 bg-surface-800 p-12 text-center">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-surface-700 text-xl">
        {icon}
      </div>
      <h3 className="text-lg font-medium text-zinc-200">{title}</h3>
      {description && <p className="mt-1 text-sm text-zinc-400">{description}</p>}
      {action && (
        <Link
          to={action.to}
          className="mt-4 inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400"
        >
          {action.label}
        </Link>
      )}
    </div>
  );
}

/** First line of a per-platform failure, for compact list rows. */
export function shortError(text, max = 140) {
  if (!text) return '';
  const line = String(text).split('\n')[0];
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}
