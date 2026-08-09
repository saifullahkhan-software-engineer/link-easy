import { Link } from 'react-router-dom';

export function formatWhatsAppLastScan(dateStr, now = Date.now()) {
  if (!dateStr) return 'Never';
  const timestamp = new Date(dateStr).getTime();
  if (!Number.isFinite(timestamp)) return 'Never';
  const diffMs = now - timestamp;
  if (diffMs < 60_000) return 'Just now';
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function formatWhatsAppRemaining(totalSeconds) {
  if (totalSeconds == null || totalSeconds < 0) return null;
  if (totalSeconds === 0) return 'Due now';
  const days = Math.floor(totalSeconds / 86_400);
  const hours = Math.floor((totalSeconds % 86_400) / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  if (days > 0) return `in ${days}d ${hours}h ${minutes}m ${seconds}s`;
  if (hours > 0) return `in ${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `in ${minutes}m ${seconds}s`;
  return `in ${seconds}s`;
}

const statusStyles = {
  active: 'bg-green-500/10 text-green-300 ring-green-500/25',
  paused: 'bg-yellow-500/10 text-yellow-300 ring-yellow-500/25',
  draft: 'bg-zinc-500/10 text-zinc-300 ring-zinc-500/25',
};

function FilterStatus({ status }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
        statusStyles[status] || statusStyles.draft
      }`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {status || 'draft'}
    </span>
  );
}

export default function WhatsAppFilterCard({ filter, now = Date.now(), onPause, onResume, onDelete }) {
  const remaining = filter.remaining_seconds != null
    ? Math.max(0, filter.remaining_seconds - Math.floor((now - new Date(filter.updated_at || now).getTime()) / 1000))
    : filter.next_scan_at
      ? Math.max(0, Math.floor((new Date(filter.next_scan_at).getTime() - now) / 1000))
      : null;

  const criteria = [
    filter.role && `Role: ${filter.role}`,
    filter.job_title && `Title: ${filter.job_title}`,
    filter.experience_level && `${filter.experience_level} level`,
    ...(filter.keywords || []).slice(0, 3).map((keyword) => keyword),
  ].filter(Boolean);

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-800 p-5 transition hover:border-surface-600">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <Link to={`/app/whatsapp-scanner/jobs/${filter.id}`} className="min-w-0 flex-1">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 text-xl" aria-hidden="true">💬</span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="truncate text-lg font-semibold text-zinc-100">{filter.name}</h3>
                <FilterStatus status={filter.status} />
              </div>
              <p className="mt-1 text-sm text-zinc-400">
                Every {filter.interval_hours}h · latest {filter.latest_messages_limit || 20}/group · {filter.monitored_group_names?.length || 0} group{filter.monitored_group_names?.length === 1 ? '' : 's'}
              </p>
            </div>
          </div>
        </Link>

        <div className="flex shrink-0 items-center gap-2">
          {filter.status === 'active' ? (
            <button
              onClick={() => onPause?.(filter.id)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-yellow-500/20 bg-yellow-500/10 px-3 py-1.5 text-sm font-medium text-yellow-300 transition hover:bg-yellow-500/15"
              title="Pause this filter"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 5.25v13.5m7.5-13.5v13.5" />
              </svg>
              Pause
            </button>
          ) : (
            <button
              onClick={() => onResume?.(filter.id)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-green-500/20 bg-green-500/10 px-3 py-1.5 text-sm font-medium text-green-300 transition hover:bg-green-500/15"
              title={filter.status === 'paused' ? 'Resume this filter' : 'Start this filter'}
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 5.25 10.5 6.75-10.5 6.75V5.25Z" />
              </svg>
              {filter.status === 'paused' ? 'Resume' : 'Start'}
            </button>
          )}
          <button
            onClick={() => onDelete?.(filter)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-1.5 text-sm font-medium text-red-300 transition hover:bg-red-500/15"
            title="Delete this filter"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="m6 7.5.8 12.1a1.5 1.5 0 0 0 1.5 1.4h7.4a1.5 1.5 0 0 0 1.5-1.4L18 7.5M4.5 7.5h15m-9.75 0V5.25A1.5 1.5 0 0 1 6.25 3.75h3.5a1.5 1.5 0 0 1 1.5 1.5V7.5" />
            </svg>
            Delete
          </button>
        </div>
      </div>

      <Link to={`/app/whatsapp-scanner/jobs/${filter.id}`} className="mt-4 block border-t border-surface-700 pt-4">
        <div className="grid gap-3 text-sm sm:grid-cols-3">
          <div>
            <p className="text-xs text-zinc-500">Last scan</p>
            <p className="mt-1 text-zinc-300">{formatWhatsAppLastScan(filter.last_scan_at, now)}</p>
          </div>
          <div>
            <p className="text-xs text-zinc-500">Next scan</p>
            <p className="mt-1 text-zinc-300">
              {filter.status === 'active' && remaining != null
                ? formatWhatsAppRemaining(remaining)
                : filter.status === 'paused' && remaining != null
                  ? `paused · ${formatWhatsAppRemaining(remaining)}`
                  : 'Not scheduled'}
            </p>
          </div>
          <div>
            <p className="text-xs text-zinc-500">Results</p>
            <p className="mt-1 text-zinc-300">
              <span className="text-green-300">{filter.matched_count || 0} matched</span>
              <span className="mx-1 text-zinc-600">·</span>
              {filter.total_count || 0} total
            </p>
          </div>
        </div>
        {criteria.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-1.5">
            {criteria.map((item, index) => (
              <span
                key={`${item}-${index}`}
                className="rounded bg-accent-500/10 px-2 py-0.5 text-xs text-accent-300"
              >
                {item}
              </span>
            ))}
            {filter.keywords && filter.keywords.length > 3 && (
              <span className="rounded bg-surface-700 px-2 py-0.5 text-xs text-zinc-400">
                +{filter.keywords.length - 3} more
              </span>
            )}
          </div>
        )}
      </Link>
    </div>
  );
}
