/**
 * Gmail visual bits shared by the Gmail inbox and compose pages.
 */
import { formatDateTime, formatRelative } from '../social/SocialBits';

export function GmailMark({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M1.5 5.25A2.25 2.25 0 0 1 3.75 3h16.5a2.25 2.25 0 0 1 2.25 2.25v13.5A2.25 2.25 0 0 1 20.25 21H3.75a2.25 2.25 0 0 1-2.25-2.25V5.25Zm1.5.66v12.84c0 .41.34.75.75.75h16.5c.41 0 .75-.34.75-.75V5.91l-8.28 6.07a1.5 1.5 0 0 1-1.68 0L3 5.91Zm1.03-.66L12 11.32l7.97-6.07H4.03Z" />
    </svg>
  );
}

export function GmailStatusBadge({ status }) {
  if (!status || status.connected === false) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 px-2.5 py-1 text-xs font-medium text-zinc-300 ring-1 ring-inset ring-zinc-500/20">
        <span className="h-1.5 w-1.5 rounded-full bg-zinc-400" />
        Not connected
      </span>
    );
  }
  if (status?.reconnect_required) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-300 ring-1 ring-inset ring-amber-500/30">
        <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
        Reconnect needed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
      Connected
    </span>
  );
}

/** Avatar circle with the sender's initial. */
export function Avatar({ name, email, className = 'h-9 w-9 text-xs' }) {
  const label = (name || email || '?').trim().charAt(0).toUpperCase() || '?';
  return (
    <div
      className={`flex shrink-0 items-center justify-center rounded-full bg-accent-500/15 font-bold text-accent-300 ${className}`}
      title={name || email}
    >
      {label}
    </div>
  );
}

const LABEL_STYLES = [
  'bg-rose-500/10 text-rose-300 ring-rose-500/25',
  'bg-amber-500/10 text-amber-300 ring-amber-500/25',
  'bg-emerald-500/10 text-emerald-300 ring-emerald-500/25',
  'bg-sky-500/10 text-sky-300 ring-sky-500/25',
  'bg-violet-500/10 text-violet-300 ring-violet-500/25',
  'bg-pink-500/10 text-pink-300 ring-pink-500/25',
  'bg-cyan-500/10 text-cyan-300 ring-cyan-500/25',
  'bg-lime-500/10 text-lime-300 ring-lime-500/25',
];

function hashIndex(value) {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  return hash;
}

/** Tiny label pill (custom labels only — system labels have icons instead). */
export function LabelChip({ label }) {
  return (
    <span
      title={label.name}
      className={`inline-flex max-w-[8rem] items-center truncate rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset ${LABEL_STYLES[hashIndex(label.id) % LABEL_STYLES.length]}`}
    >
      {label.name}
    </span>
  );
}

export function formatDateTimeSafe(value) {
  return formatDateTime(value);
}

export function formatRelativeSafe(value, now) {
  return formatRelative(value, now);
}
