import { LeadStatusBadge } from '../Badge';

const STEP_LABELS = {
  visit_profile: 'Visit Profile',
  like_post: 'Like Post',
  visit_and_like: 'Visit & Like',
  send_connection: 'Connection Request',
  send_message: 'Send Message',
  follow_up_if_pending: 'Follow up if pending',
  thanks_if_accepted: 'Thanks if accepted',
};

// Where the lead came from. Feed Scroll matches, CSV imports and manual entries
// all live in the same table — this only labels their origin.
const SOURCE_TAGS = {
  job_feed_scan: {
    label: 'Feed scan',
    className: 'bg-sky-500/10 text-sky-300 ring-sky-500/20',
  },
  csv_import: {
    label: 'CSV',
    className: 'bg-violet-500/10 text-violet-300 ring-violet-500/20',
  },
  manual: {
    label: 'Manual',
    className: 'bg-zinc-500/10 text-zinc-400 ring-zinc-500/20',
  },
};

function sourceTitle(lead) {
  const parts = [];
  if (lead.source === 'job_feed_scan') {
    parts.push('Saved from a Feed Scroll match');
    if (lead.matched_score != null) parts.push(`Score: ${Number(lead.matched_score).toFixed(1)}`);
    if (lead.matched_criteria?.length) parts.push(`Matched: ${lead.matched_criteria.join(', ')}`);
    if (lead.source_post_url) parts.push(lead.source_post_url);
  } else if (lead.source === 'csv_import') {
    parts.push('Imported from a CSV upload');
  } else if (lead.source === 'manual') {
    parts.push('Added manually');
  }
  return parts.join('\n');
}

function SourceTag({ lead }) {
  const tag = SOURCE_TAGS[lead.source];
  if (!tag) return null;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset ${tag.className}`}
      title={sourceTitle(lead)}
    >
      {tag.label}
    </span>
  );
}

function formatRemaining(nextActionAt, now) {
  const nextAt = Date.parse(nextActionAt);
  if (!Number.isFinite(nextAt)) return null;
  const totalSeconds = Math.max(0, Math.floor((nextAt - now) / 1000));
  if (totalSeconds === 0) return 'Due now';
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (days) return `${days}d ${hours}h ${minutes}m`;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

/** Leads table for the selected campaign. Polling lives in the parent. */
export default function LeadsTable({ leads, loading, steps = [], now = Date.now() }) {
  if (loading && leads.length === 0) {
    return (
      <div className="animate-pulse space-y-2 p-4">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-9 rounded bg-surface-700/60" />
        ))}
      </div>
    );
  }

  if (leads.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
        <svg className="mb-3 h-10 w-10 text-zinc-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4">
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
        </svg>
        <p className="text-sm font-medium text-zinc-400">No leads yet</p>
        <p className="mt-1 text-xs text-zinc-600">
          Add prospects manually, import a CSV, or pull in saved feed leads — then start the
          campaign.
        </p>
      </div>
    );
  }

  return (
    <div className="scrollbar-thin overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-surface-700 text-xs uppercase tracking-wide text-zinc-500">
            <th className="px-4 py-3 font-medium">Name</th>
            <th className="px-4 py-3 font-medium">LinkedIn</th>
            <th className="px-4 py-3 font-medium">Status</th>
            <th className="px-4 py-3 font-medium">Next step</th>
            <th className="px-4 py-3 font-medium">Scheduled time</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-surface-700/70">
          {leads.map((lead) => {
            const name = [lead.first_name, lead.last_name].filter(Boolean).join(' ') || '—';
            const nextStep = steps.find(
              (step) => Number(step.step_order) === Number(lead.current_step)
            );
            const nextStepLabel = nextStep
              ? (STEP_LABELS[nextStep.step_type] || nextStep.step_type)
              : null;
            const remaining = formatRemaining(lead.next_action_at, now);
            const isFinished = ['complete', 'failed'].includes(lead.status);
            return (
              <tr key={lead.id} className="transition hover:bg-surface-800/60">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <p className="font-medium text-zinc-200">{name}</p>
                    <SourceTag lead={lead} />
                  </div>
                  {lead.headline && (
                    <p className="mt-0.5 max-w-[260px] truncate text-xs text-zinc-500">{lead.headline}</p>
                  )}
                </td>
                <td className="px-4 py-3">
                  <a
                    href={lead.linkedin_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="inline-flex max-w-[220px] items-center gap-1 truncate text-accent-400 hover:text-accent-300 hover:underline"
                  >
                    <span className="truncate">{lead.linkedin_url.replace(/^https?:\/\/(www\.)?/, '')}</span>
                    <svg className="h-3 w-3 shrink-0" viewBox="0 0 20 20" fill="currentColor">
                      <path fillRule="evenodd" d="M4.25 5.5a.75.75 0 0 0-.75.75v8.5c0 .414.336.75.75.75h8.5a.75.75 0 0 0 .75-.75v-4a.75.75 0 0 1 1.5 0v4A2.25 2.25 0 0 1 12.75 17h-8.5A2.25 2.25 0 0 1 2 14.75v-8.5A2.25 2.25 0 0 1 4.25 4h5a.75.75 0 0 1 0 1.5h-5Z" clipRule="evenodd" />
                      <path fillRule="evenodd" d="M6.194 12.753a.75.75 0 0 0 1.06.053L16.5 4.44v2.81a.75.75 0 0 0 1.5 0v-4.5a.75.75 0 0 0-.75-.75h-4.5a.75.75 0 0 0 0 1.5h2.553l-9.056 8.194a.75.75 0 0 0-.053 1.06Z" clipRule="evenodd" />
                    </svg>
                  </a>
                </td>
                <td className="px-4 py-3">
                  <LeadStatusBadge status={lead.status} />
                </td>
                <td className="px-4 py-3">
                  {nextStepLabel && !isFinished ? (
                    <div>
                      <p className="font-medium text-zinc-300">{nextStepLabel}</p>
                      <p className="text-[10px] text-zinc-600">Step {lead.current_step}</p>
                    </div>
                  ) : (
                    <span className="text-zinc-500">{isFinished ? 'No next step' : 'Not scheduled'}</span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs">
                  {lead.next_action_at && !isFinished ? (
                    <div>
                      <p className="font-mono font-semibold tabular-nums text-emerald-300">{remaining}</p>
                      <p className="mt-0.5 whitespace-nowrap text-[10px] text-zinc-500" title={new Date(lead.next_action_at).toISOString()}>
                        {new Date(lead.next_action_at).toLocaleString(undefined, {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </p>
                    </div>
                  ) : (
                    <span className="text-zinc-500">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
