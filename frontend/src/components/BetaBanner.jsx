import { useEffect, useState } from 'react';

const STORAGE_KEY = 'linkeasy_beta_notice_dismissed';

/**
 * Global "free beta" notice.
 *
 * LinkEasy actions (LinkedIn logins, WhatsApp QR, scans, live chat) run in a
 * real cloud browser on shared/free infrastructure, so responses can be
 * delayed. This banner sets that expectation on every app page. It can be
 * dismissed; the dismissal is persisted in localStorage.
 */
export default function BetaBanner() {
  const [dismissed, setDismissed] = useState(true); // avoid flash before hydration

  useEffect(() => {
    try {
      setDismissed(window.localStorage.getItem(STORAGE_KEY) === '1');
    } catch {
      setDismissed(false);
    }
  }, []);

  function dismiss() {
    setDismissed(true);
    try {
      window.localStorage.setItem(STORAGE_KEY, '1');
    } catch {
      /* private mode — banner just returns next visit */
    }
  }

  if (dismissed) return null;

  return (
    <div className="mb-6 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 inline-flex shrink-0 items-center rounded-md bg-amber-500/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-amber-300 ring-1 ring-inset ring-amber-500/30">
          Beta
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-amber-200">
            LinkEasy is in free beta development
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-amber-200/80">
            Some responses may be delayed — connecting LinkedIn or WhatsApp launches a real
            cloud browser, which can take 30–40 seconds (longer on a cold start). Please wait
            for operations to finish, and retry if something stalls. Thank you for testing!
          </p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="shrink-0 rounded-md p-1 text-amber-300/70 transition hover:bg-amber-500/10 hover:text-amber-200"
          aria-label="Dismiss beta notice"
        >
          <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  );
}
