import { Link } from 'react-router-dom';

/**
 * Shown wherever LinkedIn automation would normally appear while the
 * feature is switched off on this instance (LINKEDIN_ENABLED=false).
 *
 * LinkedIn automation is enabled by default — campaigns, feed scans and
 * account connect all run. This notice appears only when an operator has
 * deliberately flipped the kill switch (for example while LinkedIn is
 * challenging the host's IP range), so the copy reads as a temporary
 * outage rather than an unbuilt feature.
 */
export default function LinkedInUnavailableNotice({ message, className = '' }) {
  return (
    <div
      className={`card border-amber-500/30 bg-amber-500/5 p-6 ${className}`}
      data-testid="linkedin-unavailable"
    >
      <div className="flex gap-4">
        <div className="hidden sm:flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-amber-500/10 text-xl">
          ⏸️
        </div>
        <div className="flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-amber-100">
              LinkedIn automation is temporarily paused
            </h2>
            <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-300">
              Temporarily unavailable
            </span>
          </div>

          <p className="mt-2 text-sm leading-relaxed text-zinc-300">
            {message ||
              'LinkedIn automation is temporarily unavailable on this instance. Your campaigns, leads and feed jobs are saved and can be started as soon as it is back.'}
          </p>

          <div className="mt-4 rounded-lg bg-surface-800/60 p-3 text-xs leading-relaxed text-zinc-400">
            <p className="font-medium text-zinc-300">What about my data?</p>
            <p className="mt-1">
              Nothing is lost — campaigns, leads and feed-scan jobs stay saved while LinkedIn
              automation is paused. WhatsApp automation is unaffected and remains fully available
              in the meantime.
            </p>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Link to="/app/account/whatsapp" className="btn-primary text-xs">
              Use WhatsApp automation
            </Link>
            <span className="text-xs text-zinc-500">
              WhatsApp is fully available and unaffected.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
