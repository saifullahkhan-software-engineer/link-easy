import { Link } from 'react-router-dom';

/**
 * Shared building blocks for the Accounts area.
 *
 * The hub (/app/account) is a *summary* only: one card per platform showing
 * whether it is connected and how many accounts are connected, plus a single
 * "Manage …" button. Everything else — the individual accounts, connecting
 * another one, disconnecting — lives on that platform's manage page.
 */

/* ------------------------------ platform glyphs --------------------------- */

export function WhatsAppGlyph({ className = 'h-6 w-6' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 9.75a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375m-13.5 3.01c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 0 1 .778-.332 48.294 48.294 0 0 0 5.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z" />
    </svg>
  );
}

export function LinkedInGlyph({ className = 'h-6 w-6' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M19 0h-14c-2.761 0-5 2.239-5 5v14c0 2.761 2.239 5 5 5h14c2.762 0 5-2.239 5-5v-14c0-2.761-2.238-5-5-5zm-11 19h-3v-11h3v11zm-1.5-12.268c-.966 0-1.75-.79-1.75-1.764s.784-1.764 1.75-1.764 1.75.79 1.75 1.764-.783 1.764-1.75 1.764zm13.5 12.268h-3v-5.604c0-3.368-4-3.113-4 0v5.604h-3v-11h3v1.765c1.396-2.586 7-2.777 7 2.476v6.759z" />
    </svg>
  );
}

export function GmailGlyph({ className = 'h-6 w-6' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M1.5 5.25A2.25 2.25 0 0 1 3.75 3h16.5a2.25 2.25 0 0 1 2.25 2.25v13.5A2.25 2.25 0 0 1 20.25 21H3.75a2.25 2.25 0 0 1-2.25-2.25V5.25Zm1.5.66v12.84c0 .41.34.75.75.75h16.5c.41 0 .75-.34.75-.75V5.91l-8.28 6.07a1.5 1.5 0 0 1-1.68 0L3 5.91Zm1.03-.66L12 11.32l7.97-6.07H4.03Z" />
    </svg>
  );
}

/* -------------------------------- status pill ----------------------------- */

const PILLS = {
  connected: { text: 'Connected', dot: 'bg-emerald-400', cls: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30' },
  attention: { text: 'Reconnect needed', dot: 'bg-amber-400', cls: 'bg-amber-500/10 text-amber-300 ring-amber-500/30' },
  pending: { text: 'Action needed', dot: 'bg-amber-400', cls: 'bg-amber-500/10 text-amber-300 ring-amber-500/30' },
  none: { text: 'Not connected', dot: 'bg-zinc-400', cls: 'bg-zinc-500/10 text-zinc-300 ring-zinc-500/20' },
  soon: { text: 'Coming soon', dot: 'bg-amber-400', cls: 'bg-amber-500/10 text-amber-300 ring-amber-500/20' },
};

/** Small "Connected / Not connected" pill used across the Accounts area. */
export function ConnectionStatusPill({ state = 'none', label }) {
  const pill = PILLS[state] || PILLS.none;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${pill.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${pill.dot}`} />
      {label || pill.text}
    </span>
  );
}

/** "3 profiles connected" / "No profile connected yet". */
export function connectionCountLabel(count, noun = { one: 'account', many: 'accounts' }) {
  if (!count) return `No ${noun.one} connected yet`;
  return `${count} ${count === 1 ? noun.one : noun.many} connected`;
}

/* ------------------------------- summary card ----------------------------- */

/**
 * One platform on the Accounts hub. Deliberately shows *no* account details
 * and *no* connect action — only the status, the number of connected accounts
 * and the button that opens the platform's manage page.
 */
export function PlatformSummaryCard({
  icon,
  iconClass = 'bg-surface-800 text-zinc-200',
  title,
  hint,
  state = 'none',
  statusLabel,
  count = 0,
  noun,
  manageTo,
  manageLabel = 'Manage accounts',
  loading = false,
  disabled = false,
  testId,
}) {
  return (
    <article className="card flex h-full min-w-0 flex-col p-5" data-testid={testId}>
      <div className="flex items-start justify-between gap-3">
        <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${iconClass}`}>{icon}</div>
        {loading ? (
          <span className="h-6 w-24 animate-pulse rounded-full bg-surface-700" />
        ) : (
          <ConnectionStatusPill state={state} label={statusLabel} />
        )}
      </div>

      <h3 className="mt-4 text-base font-semibold text-zinc-100">{title}</h3>
      {loading ? (
        <p className="mt-2 h-4 w-32 animate-pulse rounded bg-surface-700" />
      ) : (
        <p className="mt-1 text-sm text-zinc-400">{connectionCountLabel(count, noun)}</p>
      )}
      {hint && <p className="mt-1 text-xs text-zinc-500">{hint}</p>}

      <div className="mt-auto pt-5">
        {disabled ? (
          <button type="button" className="btn-secondary min-h-11 w-full justify-center" disabled>
            {manageLabel}
          </button>
        ) : (
          <Link to={manageTo} className="btn-primary min-h-11 w-full justify-center">
            {manageLabel}
          </Link>
        )}
      </div>
    </article>
  );
}

/* ---------------------------- connected account card ---------------------- */

/**
 * One connected account on a manage page — the LinkedIn manage card design
 * (avatar, name + status badge, a small detail grid and its own actions),
 * reused by every platform so all manage pages look the same.
 */
export function ConnectedAccountCard({
  icon,
  iconClass = 'bg-surface-800 text-zinc-200',
  title,
  subtitle,
  badge,
  details = [],
  children,
  actions,
  highlighted = false,
  testId,
}) {
  return (
    <article
      className={`card flex h-full min-w-0 flex-col p-5 ${highlighted ? 'ring-1 ring-accent-500/40' : ''}`}
      data-testid={testId}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl text-lg font-bold ${iconClass}`}>
            {icon}
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-base font-semibold text-zinc-100">{title}</h3>
            {subtitle && <p className="mt-0.5 truncate text-sm text-zinc-400">{subtitle}</p>}
          </div>
        </div>
        {badge}
      </div>

      {details.length > 0 && (
        <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-surface-700 pt-4 text-sm">
          {details.map((item) => (
            <div key={item.label} className="min-w-0">
              <dt className="text-[11px] uppercase tracking-wide text-zinc-500">{item.label}</dt>
              <dd className="mt-0.5 truncate text-zinc-300">{item.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {children}

      {actions && <div className="mt-auto flex flex-wrap gap-2 border-t border-surface-700 pt-4">{actions}</div>}
    </article>
  );
}

/** Dashed "add another" tile shown next to the connected account cards. */
export function AddAccountCard({ label, hint, onClick, to, disabled = false, testId }) {
  const body = (
    <>
      <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-surface-800 text-2xl leading-none text-accent-300">+</span>
      <span className="mt-3 text-sm font-semibold text-zinc-200">{label}</span>
      {hint && <span className="mt-1 text-xs text-zinc-500">{hint}</span>}
    </>
  );
  const className =
    'card flex h-full min-h-[190px] min-w-0 flex-col items-center justify-center border-dashed p-5 text-center transition hover:border-accent-500/50 disabled:cursor-not-allowed disabled:opacity-50';
  if (to) {
    return (
      <Link to={to} className={className} data-testid={testId}>
        {body}
      </Link>
    );
  }
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={className} data-testid={testId}>
      {body}
    </button>
  );
}
