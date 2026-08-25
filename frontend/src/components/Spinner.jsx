export function Spinner({ className = 'h-4 w-4' }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-90"
        fill="currentColor"
        d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

/**
 * Explicit progress panel for genuinely slow operations (browser-automated
 * LinkedIn logins). Never a bare spinner — always with messaging.
 */
export function SlowOperationNotice({ title, hint, elapsedSeconds }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-accent-500/30 bg-accent-500/5 px-4 py-3">
      <Spinner className="mt-0.5 h-5 w-5 text-accent-400" />
      <div className="min-w-0">
        <p className="text-sm font-medium text-accent-200">{title}</p>
        {hint && <p className="mt-0.5 text-xs text-zinc-400">{hint}</p>}
        {typeof elapsedSeconds === 'number' && (
          <p className="mt-1 text-xs tabular-nums text-zinc-500">
            {elapsedSeconds}s elapsed
            {elapsedSeconds > 45
              ? ' — still working, LinkedIn can be slow on the free beta'
              : ' — normal, connections usually take 30–40s (up to 2 minutes on a cold start)'}
          </p>
        )}
      </div>
    </div>
  );
}
