import { useEffect, useState } from 'react';
import { useFeatures } from '../hooks/useFeatures';

const STORAGE_KEY = 'linkeasy_hosted_demo_notice_dismissed';

/**
 * "You're on the hosted demo — LinkEasy works best locally" notice.
 *
 * Shown ONLY when the backend reports ENVIRONMENT=deployment (`is_demo`).
 * Self-hosted and local installs never see it, which is why the flag comes
 * from the API rather than a build-time env var: the same frontend bundle is
 * served in both cases.
 *
 * The hosted demo is deliberately reduced — no Celery Beat, so no scheduled
 * campaign steps and no recurring scans, and no residential proxies, so no
 * LinkedIn. Running locally lifts all of that, so the banner points there and
 * offers a contact address for setup help.
 *
 * Dismissible, persisted in localStorage, same pattern as BetaBanner.
 */
export default function HostedDemoBanner() {
  const { isDemo, deploymentNotice, supportEmail, loading } = useFeatures();
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

  if (loading || !isDemo || dismissed) return null;

  return (
    <div
      className="mb-6 rounded-lg border border-sky-500/30 bg-sky-500/10 px-4 py-3"
      data-testid="hosted-demo-banner"
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 inline-flex shrink-0 items-center rounded-md bg-sky-500/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-sky-300 ring-1 ring-inset ring-sky-500/30">
          Demo
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-sky-200">
            LinkEasy runs best on your own machine
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-sky-200/80">
            {deploymentNotice}
          </p>
          {supportEmail && (
            <p className="mt-2 text-xs text-sky-200/80">
              Need a hand?{' '}
              <a
                href={`mailto:${supportEmail}?subject=${encodeURIComponent(
                  'Help setting up LinkEasy locally'
                )}`}
                className="font-semibold text-sky-300 underline underline-offset-2 hover:text-sky-200"
              >
                {supportEmail}
              </a>{' '}
              — we'll help you get it running.
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="shrink-0 rounded-md p-1 text-sky-300/70 transition hover:bg-sky-500/10 hover:text-sky-200"
          aria-label="Dismiss hosted demo notice"
        >
          <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  );
}
