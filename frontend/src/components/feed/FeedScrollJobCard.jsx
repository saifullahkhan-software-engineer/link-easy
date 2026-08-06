import { Link } from 'react-router-dom';
import ScoreBadge from './ScoreBadge';

export default function FeedScrollJobCard({ job, onPause, onResume, onDelete }) {
  const statusColor = {
    active: 'text-green-400 bg-green-500/10 ring-green-500/20',
    paused: 'text-yellow-400 bg-yellow-500/10 ring-yellow-500/20',
    draft: 'text-zinc-400 bg-zinc-500/10 ring-zinc-500/20',
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return 'Never';
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    return `${diffDays}d ago`;
  };

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-800 p-5 transition hover:border-surface-600">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xl">{job.mode === 'job_search' ? '🔍' : '📝'}</span>
            <h3 className="truncate text-lg font-semibold text-zinc-100">{job.name}</h3>
          </div>

          <div className="flex flex-wrap items-center gap-2 mb-3">
            <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${statusColor[job.status]}`}>
              {job.status}
            </span>
            <span className="text-sm text-zinc-400">
              Mode: {job.mode === 'job_search' ? 'Job Search' : 'Post Search'}
            </span>
            <span className="text-sm text-zinc-400">•</span>
            <span className="text-sm text-zinc-400">Interval: {job.feed_interval_hours}h</span>
          </div>

          <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-zinc-500">
            <span>Last scan: {formatDate(job.last_scanned_at)}</span>
            {job.next_scan_at && job.status === 'active' && (
              <>
                <span>•</span>
                <span>Next scan: {formatDate(job.next_scan_at)}</span>
              </>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <Link
            to={`/app/feed-scroll/jobs/${job.id}`}
            className="inline-flex items-center justify-center rounded-lg border border-surface-700 bg-surface-700 px-3 py-1.5 text-sm font-medium text-zinc-200 transition hover:bg-surface-600 hover:text-zinc-100"
          >
            View
          </Link>
          <Link
            to={`/app/feed-scroll/jobs/${job.id}/edit`}
            className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-surface-700 bg-surface-800 px-3 py-1.5 text-sm font-medium text-zinc-300 transition hover:bg-surface-700 hover:text-zinc-100"
            title="Edit keywords, experience, and job titles for the next scan"
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10" />
            </svg>
            Edit
          </Link>
          {job.status === 'active' ? (
            <button
              onClick={() => onPause(job.id)}
              className="inline-flex items-center justify-center rounded-lg border border-yellow-500/20 bg-yellow-500/10 px-3 py-1.5 text-sm font-medium text-yellow-300 transition hover:bg-yellow-500/15"
            >
              Pause
            </button>
          ) : (
            <button
              onClick={() => onResume(job.id)}
              className="inline-flex items-center justify-center rounded-lg border border-green-500/20 bg-green-500/10 px-3 py-1.5 text-sm font-medium text-green-300 transition hover:bg-green-500/15"
            >
              Resume
            </button>
          )}
          <button
            onClick={() => onDelete?.(job)}
            className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-1.5 text-sm font-medium text-red-300 transition hover:bg-red-500/15"
            title="Delete this job and all its results"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0Z" />
            </svg>
            Delete
          </button>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5 border-t border-surface-700 pt-3">
        {job.mode === 'post_search' && job.keywords?.map((keyword) => (
          <span key={keyword} className="rounded bg-accent-500/10 px-2 py-0.5 text-xs text-accent-300">{keyword}</span>
        ))}
        {job.mode === 'job_search' && (job.experience_min_years != null || job.experience_max_years != null) && (
          <span className="rounded bg-surface-700 px-2 py-0.5 text-xs text-zinc-300">Experience: {job.experience_min_years ?? 0}–{job.experience_max_years ?? 'any'} years</span>
        )}
      </div>

      {job.mode === 'job_search' && (
        <div className="mt-3 flex flex-wrap gap-1.5 border-t border-surface-700 pt-3">
          {job.job_titles?.slice(0, 3).map((title) => (
            <span key={title} className="rounded bg-surface-700 px-2 py-0.5 text-xs text-zinc-300">
              {title}
            </span>
          ))}
          {job.skill_set?.slice(0, 3).map((skill) => (
            <span key={skill} className="rounded bg-accent-500/10 px-2 py-0.5 text-xs text-accent-300">
              {skill}
            </span>
          ))}
          {job.keywords?.slice(0, 3).map((keyword) => (
            <span key={keyword} className="rounded bg-amber-500/10 px-2 py-0.5 text-xs text-amber-300">
              Keyword: {keyword}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
