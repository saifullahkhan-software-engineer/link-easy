import { useFeatures } from '../hooks/useFeatures';

/**
 * Inline notice for pages that manage recurring/scheduled scans, shown when
 * the backend has timer-driven work switched off (SCHEDULED_JOBS_ENABLED=false
 * and Celery Beat stopped for that instance).
 *
 * Renders nothing when scheduling is available, so local and self-hosted
 * installs see no change at all.
 *
 * The point is to set expectations *before* the user configures an interval
 * and presses Start, rather than letting them discover it via a 503.
 */
export default function SchedulingDisabledNotice({ className = '' }) {
  const { scheduledJobsEnabled, scheduledJobsMessage, supportEmail, loading } =
    useFeatures();

  if (loading || scheduledJobsEnabled) return null;

  return (
    <div
      className={`rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 ${className}`}
      data-testid="scheduling-disabled"
    >
      <p className="text-sm font-medium text-amber-200">
        Scheduled scans are temporarily off
      </p>
      <p className="mt-1 text-xs leading-relaxed text-amber-200/80">
        {scheduledJobsMessage ||
          'Recurring jobs are temporarily disabled on this instance. You can still run a scan on demand and start campaigns manually.'}
      </p>
      {supportEmail && (
        <p className="mt-2 text-xs text-amber-200/80">
          Want the full version running locally?{' '}
          <a
            href={`mailto:${supportEmail}?subject=${encodeURIComponent(
              'Help setting up LinkEasy locally'
            )}`}
            className="font-semibold text-amber-300 underline underline-offset-2 hover:text-amber-200"
          >
            {supportEmail}
          </a>
        </p>
      )}
    </div>
  );
}
