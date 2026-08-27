import { Link } from 'react-router-dom';

/**
 * Shown wherever LinkedIn automation would normally appear while the feature
 * is disabled.
 *
 * Why it exists: LinkedIn reliably blocks sign-ins that come from datacenter
 * IP addresses, so running it from our host produces CAPTCHAs and security
 * checkpoints instead of connected accounts. Rather than show a form that
 * cannot succeed, we explain the constraint and point users at WhatsApp,
 * which is unaffected.
 */
export default function LinkedInUnavailableNotice({ message, className = '' }) {
  return (
    <div
      className={`card border-amber-500/30 bg-amber-500/5 p-6 ${className}`}
      data-testid="linkedin-unavailable"
    >
      <div className="flex gap-4">
        <div className="hidden sm:flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-amber-500/10 text-xl">
          🔒
        </div>
        <div className="flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-amber-100">
              LinkedIn automation is coming soon
            </h2>
            <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-300">
              Needs proxy setup
            </span>
          </div>

          <p className="mt-2 text-sm leading-relaxed text-zinc-300">
            {message}
          </p>

          <div className="mt-4 rounded-lg bg-surface-800/60 p-3 text-xs leading-relaxed text-zinc-400">
            <p className="font-medium text-zinc-300">Why the wait?</p>
            <p className="mt-1">
              LinkedIn flags logins coming from cloud/server IP addresses and answers them with a
              CAPTCHA or a security checkpoint. Running it safely means giving every account its own
              dedicated residential proxy — an ongoing cost we&apos;re adding once the product is
              monetised or has enough users to support it. Turning it on then takes a config change,
              not a rebuild.
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
