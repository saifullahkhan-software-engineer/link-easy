const base =
  'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap';

/** Badge for LinkedIn account connection status. */
export function AccountStatusBadge({ status }) {
  const map = {
    pending_verification: {
      label: 'Verification needed',
      cls: 'text-amber-300 bg-amber-500/10 ring-amber-500/30',
      dot: 'bg-amber-400',
    },
    active: {
      label: 'Connected',
      cls: 'text-emerald-300 bg-emerald-500/10 ring-emerald-500/30',
      dot: 'bg-emerald-400',
    },
    valid: {
      label: 'Connected',
      cls: 'text-emerald-300 bg-emerald-500/10 ring-emerald-500/30',
      dot: 'bg-emerald-400',
    },
    failed: {
      label: 'Login failed',
      cls: 'text-red-300 bg-red-500/10 ring-red-500/30',
      dot: 'bg-red-400',
    },
    suspended: {
      label: 'Suspended',
      cls: 'text-red-300 bg-red-500/10 ring-red-500/30',
      dot: 'bg-red-400',
    },
  };
  const s = map[status] || {
    label: status || 'Unknown',
    cls: 'text-zinc-300 bg-zinc-500/10 ring-zinc-500/30',
    dot: 'bg-zinc-400',
  };
  return (
    <span className={`${base} ${s.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

/** Badge for a lead's position in the outreach sequence. */
export function LeadStatusBadge({ status }) {
  const map = {
    pending: 'text-zinc-300 bg-zinc-500/10 ring-zinc-500/30',
    visiting: 'text-blue-300 bg-blue-500/10 ring-blue-500/30',
    requested: 'text-indigo-300 bg-indigo-500/10 ring-indigo-500/30',
    accepted: 'text-teal-300 bg-teal-500/10 ring-teal-500/30',
    messaged: 'text-purple-300 bg-purple-500/10 ring-purple-500/30',
    replied: 'text-emerald-200 bg-emerald-500/20 ring-emerald-400/40 font-semibold',
    skipped: 'text-amber-300 bg-amber-500/10 ring-amber-500/30',
    failed: 'text-red-300 bg-red-500/10 ring-red-500/30',
    complete: 'text-emerald-300 bg-transparent ring-emerald-500/50',
  };
  return (
    <span className={`${base} ${map[status] || map.pending}`}>
      {status || 'unknown'}
    </span>
  );
}

/** Badge for campaign lifecycle status. */
export function CampaignStatusBadge({ status }) {
  const map = {
    draft: 'text-zinc-300 bg-zinc-500/10 ring-zinc-500/30',
    active: 'text-emerald-300 bg-emerald-500/10 ring-emerald-500/30',
    paused: 'text-amber-300 bg-amber-500/10 ring-amber-500/30',
    completed: 'text-accent-300 bg-accent-500/10 ring-accent-500/30',
  };
  return <span className={`${base} ${map[status] || map.draft}`}>{status}</span>;
}
