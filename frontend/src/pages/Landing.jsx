import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useAdminAccess } from '../hooks/useAdminAccess';

const features = [
  {
    title: 'Connect your social tools',
    body: 'Bring LinkedIn and WhatsApp into one focused workspace without changing how your accounts are used.',
    icon: 'connect',
  },
  {
    title: 'Build campaigns in minutes',
    body: 'Turn a lead list into a clear sequence of profile visits, connection requests, and personal follow-ups.',
    icon: 'campaign',
  },
  {
    title: 'Automate the daily busywork',
    body: 'Keep outreach and message scanning moving with schedules, sensible limits, and human-like pacing.',
    icon: 'automate',
  },
  {
    title: 'Take over when it matters',
    body: 'Open live conversations, read replies, and respond yourself whenever a real human touch is needed.',
    icon: 'chat',
  },
];

const steps = [
  {
    number: '01',
    title: 'Connect',
    body: 'Securely connect the LinkedIn and WhatsApp accounts you already use.',
  },
  {
    number: '02',
    title: 'Create',
    body: 'Build an outreach campaign or a WhatsApp filter around your goal.',
  },
  {
    number: '03',
    title: 'Automate',
    body: 'Set your schedule once and let LinkEasy handle the repeatable work each day.',
  },
];

function LinkedInMark({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M5.2 7.35H1.35V22H5.2V7.35ZM3.28 1A2.25 2.25 0 1 0 3.3 5.5 2.25 2.25 0 0 0 3.28 1ZM22.65 13.6c0-4.42-2.36-6.47-5.5-6.47a4.75 4.75 0 0 0-4.3 2.37h-.06V7.35H9.1V22h3.85v-7.25c0-1.91.36-3.76 2.73-3.76 2.34 0 2.37 2.19 2.37 3.88V22h3.85l.75-8.4Z" />
    </svg>
  );
}

function WhatsAppMark({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M20.5 3.47A11.8 11.8 0 0 0 1.94 17.7L.27 23.8l6.24-1.63A11.78 11.78 0 0 0 12.15 23h.01A11.84 11.84 0 0 0 24 11.19c0-3.15-1.24-6.12-3.5-7.72Zm-8.34 17.54h-.01a9.8 9.8 0 0 1-5-1.37l-.36-.21-3.7.97.99-3.61-.24-.37A9.82 9.82 0 1 1 12.16 21Zm5.39-7.36c-.3-.15-1.75-.86-2.02-.96-.27-.1-.47-.15-.67.15-.2.3-.76.96-.93 1.16-.17.2-.34.22-.64.07-.3-.15-1.25-.46-2.38-1.47a8.9 8.9 0 0 1-1.65-2.06c-.17-.3-.02-.46.13-.6.13-.13.3-.34.44-.52.15-.17.2-.3.3-.49.1-.2.05-.37-.03-.52-.07-.15-.66-1.6-.91-2.19-.24-.57-.49-.49-.67-.5h-.57c-.2 0-.52.07-.79.37-.27.3-1.04 1.01-1.04 2.46 0 1.45 1.06 2.86 1.21 3.05.15.2 2.08 3.18 5.04 4.46.7.3 1.25.49 1.68.62.71.22 1.35.19 1.86.12.57-.09 1.75-.72 2-1.41.25-.7.25-1.3.17-1.42-.07-.12-.27-.2-.56-.35Z" />
    </svg>
  );
}

function FeatureIcon({ name }) {
  const paths = {
    connect: (
      <>
        <path d="M8 8.5h8M8 15.5h8" />
        <path d="M6 5.5A2.5 2.5 0 1 1 1 5.5a2.5 2.5 0 0 1 5 0ZM23 5.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0ZM6 18.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0ZM23 18.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z" />
      </>
    ),
    campaign: (
      <>
        <path d="M5 3.5h11l3 3v14H5z" />
        <path d="M8.5 9h7M8.5 13h7M8.5 17h4" />
      </>
    ),
    automate: (
      <>
        <path d="M12 2.5v3M12 18.5v3M21.5 12h-3M5.5 12h-3M18.72 5.28 16.6 7.4M7.4 16.6l-2.12 2.12M18.72 18.72 16.6 16.6M7.4 7.4 5.28 5.28" />
        <circle cx="12" cy="12" r="4.25" />
        <path d="m10.5 12 1 1 2.25-2.4" />
      </>
    ),
    chat: (
      <>
        <path d="M4 4h16v12H9l-5 4V4Z" />
        <path d="M8 9h8M8 12.5h5" />
      </>
    ),
  };
  return (
    <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="m4 10 3.5 3.5L16 5.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M4 10h12m-4.5-4.5L16 10l-4.5 4.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Product-shaped hero preview: connected channels feeding a daily workflow. */
function AutomationPreview() {
  return (
    <div className="relative mx-auto w-full max-w-[560px] lg:ml-auto">
      <div className="absolute -inset-8 rounded-full bg-accent-500/10 blur-3xl" />
      <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-surface-900/95 shadow-2xl shadow-black/50">
        <div className="flex items-center justify-between border-b border-surface-700 px-4 py-3 sm:px-5">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-red-400/70" />
            <span className="h-2.5 w-2.5 rounded-full bg-amber-400/70" />
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/70" />
          </div>
          <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-zinc-500">Daily workspace</span>
        </div>

        <div className="grid gap-4 p-4 sm:grid-cols-[148px_1fr] sm:p-5">
          <div className="rounded-xl border border-surface-700 bg-surface-950/70 p-3">
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-600">Connected tools</p>
            <div className="mt-3 space-y-2.5">
              <div className="flex items-center gap-2.5 rounded-lg border border-sky-500/20 bg-sky-500/5 p-2.5">
                <span className="grid h-8 w-8 place-items-center rounded-lg bg-[#0a66c2] text-white">
                  <LinkedInMark className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-zinc-200">LinkedIn</p>
                  <p className="text-[10px] text-emerald-400">Connected</p>
                </div>
              </div>
              <div className="flex items-center gap-2.5 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-2.5">
                <span className="grid h-8 w-8 place-items-center rounded-lg bg-[#25d366] text-white">
                  <WhatsAppMark className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-zinc-200">WhatsApp</p>
                  <p className="text-[10px] text-emerald-400">Connected</p>
                </div>
              </div>
            </div>
            <div className="mt-3 flex items-center gap-1.5 text-[10px] text-zinc-500">
              <span className="h-1.5 w-1.5 rounded-full bg-accent-400" />
              Ready to automate
            </div>
          </div>

          <div className="rounded-xl border border-surface-700 bg-surface-850 p-3.5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-accent-400">Campaign running</p>
                <h3 className="mt-1 text-sm font-semibold text-zinc-100">Founder outreach</h3>
              </div>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2 py-1 text-[10px] font-medium text-emerald-300 ring-1 ring-inset ring-emerald-500/20">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
                Live
              </span>
            </div>

            <div className="relative mt-4 space-y-2.5 before:absolute before:bottom-4 before:left-[15px] before:top-4 before:w-px before:bg-surface-600">
              {[
                ['Visit profile', 'Completed', 'done'],
                ['Send connection request', 'Scheduled today', 'active'],
                ['Personal follow-up', 'Wait 2 days', 'waiting'],
              ].map(([label, meta, state]) => (
                <div key={label} className="relative flex items-center gap-3 rounded-lg border border-surface-700 bg-surface-900 px-2.5 py-2">
                  <span className={`relative z-10 grid h-6 w-6 shrink-0 place-items-center rounded-full ring-4 ring-surface-900 ${state === 'done' ? 'bg-accent-500 text-surface-950' : state === 'active' ? 'bg-indigo-500 text-white' : 'bg-surface-700 text-zinc-500'}`}>
                    {state === 'done' ? <CheckIcon /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-medium text-zinc-200">{label}</p>
                    <p className="text-[10px] text-zinc-500">{meta}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="mx-4 mb-4 flex items-center justify-between gap-3 rounded-xl border border-emerald-500/15 bg-emerald-500/[0.06] px-3.5 py-3 sm:mx-5 sm:mb-5">
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-emerald-500/15 text-emerald-300">
              <WhatsAppMark className="h-4 w-4" />
            </span>
            <div>
              <p className="text-xs font-medium text-zinc-200">WhatsApp filter checked 3 groups</p>
              <p className="text-[10px] text-zinc-500">2 relevant messages found today</p>
            </div>
          </div>
          <span className="hidden rounded-md bg-surface-900 px-2 py-1 text-[10px] text-zinc-400 sm:block">Just now</span>
        </div>
      </div>

      <div className="absolute -bottom-5 -left-4 hidden items-center gap-2 rounded-xl border border-white/10 bg-surface-850 px-3 py-2.5 shadow-xl shadow-black/30 sm:flex">
        <span className="grid h-8 w-8 place-items-center rounded-full bg-accent-500/15 text-accent-300">
          <CheckIcon />
        </span>
        <div>
          <p className="text-xs font-semibold text-zinc-200">Today&apos;s tasks are moving</p>
          <p className="text-[10px] text-zinc-500">Paced safely in the background</p>
        </div>
      </div>
    </div>
  );
}

export default function Landing() {
  const { isAuthenticated } = useAuth();
  const { canSeeAdmin } = useAdminAccess();
  const primaryPath = isAuthenticated ? '/app' : '/signup';
  const primaryLabel = isAuthenticated ? 'Open App' : 'Start automating free';

  return (
    <div className="min-h-screen overflow-hidden bg-surface-950 text-zinc-100">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[760px] bg-[radial-gradient(circle_at_18%_18%,rgba(20,184,166,0.12),transparent_34%),radial-gradient(circle_at_82%_15%,rgba(99,102,241,0.10),transparent_30%)]" />

      <header className="relative z-20 border-b border-white/[0.06] bg-surface-950/70 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-6">
          <Link to="/" className="flex items-center gap-2.5" aria-label="LinkEasy home">
            <img src="/favicon.svg" alt="" className="h-7 w-7" />
            <span className="text-lg font-bold tracking-tight text-zinc-50">
              Link<span className="text-accent-400">Easy</span>
            </span>
          </Link>

          <nav className="hidden items-center gap-7 text-sm text-zinc-400 md:flex" aria-label="Main navigation">
            <a href="#features" className="transition hover:text-zinc-100">Features</a>
            <a href="#how-it-works" className="transition hover:text-zinc-100">How it works</a>
            <a href="#integrations" className="transition hover:text-zinc-100">Integrations</a>
          </nav>

          <div className="flex items-center gap-2.5">
            <Link to="/app" className="rounded-lg border border-surface-700 bg-surface-900 px-3.5 py-2 text-sm font-semibold text-zinc-300 transition hover:border-surface-600 hover:bg-surface-800 hover:text-zinc-100">
              App
            </Link>
            {canSeeAdmin && (
              <Link to="/admin" className="btn-primary" data-testid="header-admin-button">
                Admin <ArrowIcon />
              </Link>
            )}
            {!isAuthenticated && (
              <Link to="/signup" className="hidden px-3 py-2 text-sm font-medium text-zinc-400 transition hover:text-zinc-100 sm:inline-flex">Get started</Link>
            )}
          </div>
        </div>
      </header>

      <main className="relative">
        <section className="mx-auto grid max-w-7xl items-center gap-14 px-5 pb-24 pt-16 sm:px-6 sm:pt-20 lg:grid-cols-[0.92fr_1.08fr] lg:gap-16 lg:pb-28 lg:pt-24">
          <div className="relative z-10">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-accent-500/25 bg-accent-500/[0.08] px-3 py-1.5 text-xs font-medium text-accent-200">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-400 opacity-50" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-accent-400" />
              </span>
              Your outreach tools, working together
            </div>

            <h1 className="max-w-2xl text-4xl font-extrabold leading-[1.06] tracking-[-0.035em] text-zinc-50 sm:text-5xl lg:text-[64px]">
              Connect your tools.{' '}
              <span className="bg-gradient-to-r from-accent-300 via-teal-300 to-indigo-400 bg-clip-text text-transparent">
                Automate your day.
              </span>
            </h1>

            <p className="mt-6 max-w-xl text-base leading-7 text-zinc-400 sm:text-lg sm:leading-8">
              Connect LinkedIn and WhatsApp, create focused campaigns, and let LinkEasy handle the repetitive daily tasks—so you can spend more time on real conversations.
            </p>

            <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
              {/* Every signed-in user gets the app; the admin button appears
                  only for admins (or for everyone while the backend is still
                  in bootstrap mode and no roles have been assigned yet). */}
              <Link to="/app" className="btn-primary px-6 py-3.5 text-base shadow-lg shadow-accent-500/15" data-testid="landing-app-button">
                App Dashboard
                <ArrowIcon />
              </Link>
              {canSeeAdmin && (
                <Link to="/admin" className="btn-secondary px-6 py-3.5 text-base" data-testid="landing-admin-button">
                  Admin Dashboard
                  <ArrowIcon />
                </Link>
              )}
              <a href="#how-it-works" className="inline-flex items-center justify-center gap-2 rounded-lg px-5 py-3.5 text-sm font-semibold text-zinc-300 transition hover:bg-white/5 hover:text-white">
                See how it works
              </a>
            </div>

            <div className="mt-8 flex flex-wrap gap-x-5 gap-y-2 text-xs text-zinc-500">
              {['Simple account setup', 'Human-like pacing', 'You stay in control'].map((item) => (
                <span key={item} className="inline-flex items-center gap-1.5">
                  <span className="text-accent-400"><CheckIcon /></span>
                  {item}
                </span>
              ))}
            </div>
          </div>

          <AutomationPreview />
        </section>

        <section id="integrations" className="border-y border-white/[0.06] bg-white/[0.015]">
          <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-6 px-5 py-7 sm:px-6 md:flex-row">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-zinc-600">One workspace</p>
              <p className="mt-1 text-sm text-zinc-400">Connect the channels you use every day.</p>
            </div>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <div className="flex items-center gap-2.5 rounded-xl border border-surface-700 bg-surface-900 px-4 py-2.5 text-sm font-medium text-zinc-200">
                <span className="text-[#53a7f5]"><LinkedInMark /></span>
                LinkedIn
                <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-300">Ready</span>
              </div>
              <div className="flex items-center gap-2.5 rounded-xl border border-surface-700 bg-surface-900 px-4 py-2.5 text-sm font-medium text-zinc-200">
                <span className="text-[#25d366]"><WhatsAppMark /></span>
                WhatsApp
                <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-300">Ready</span>
              </div>
              <div className="flex items-center gap-2.5 rounded-xl border border-dashed border-surface-600 bg-surface-900/50 px-4 py-2.5 text-sm text-zinc-500">
                <svg className="h-5 w-5" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                  <path d="M10 4v12M4 10h12" strokeLinecap="round" />
                </svg>
                More workflows over time
              </div>
            </div>
          </div>
        </section>

        <section id="how-it-works" className="mx-auto max-w-7xl px-5 py-24 sm:px-6 lg:py-28">
          <div className="max-w-2xl">
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent-400">How it works</p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-zinc-50 sm:text-4xl">From connected to automated in three steps</h2>
            <p className="mt-4 text-base leading-7 text-zinc-400">No complicated automation map. Start with your accounts, define the outcome, and keep control from one dashboard.</p>
          </div>

          <div className="relative mt-14 grid gap-5 md:grid-cols-3">
            <div className="absolute left-[16.66%] right-[16.66%] top-8 hidden h-px bg-gradient-to-r from-accent-500/30 via-accent-400/50 to-indigo-500/30 md:block" />
            {steps.map((step) => (
              <article key={step.number} className="relative rounded-2xl border border-surface-700 bg-surface-900/60 p-6 transition hover:-translate-y-1 hover:border-accent-500/25 hover:bg-surface-900">
                <span className="relative z-10 grid h-16 w-16 place-items-center rounded-2xl border border-accent-500/20 bg-surface-950 text-sm font-bold text-accent-300 shadow-lg shadow-black/20">
                  {step.number}
                </span>
                <h3 className="mt-6 text-xl font-semibold text-zinc-100">{step.title}</h3>
                <p className="mt-2 text-sm leading-6 text-zinc-400">{step.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section id="features" className="border-y border-white/[0.06] bg-surface-900/45">
          <div className="mx-auto max-w-7xl px-5 py-24 sm:px-6 lg:py-28">
            <div className="flex flex-col justify-between gap-5 md:flex-row md:items-end">
              <div className="max-w-2xl">
                <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent-400">Built for the daily work</p>
                <h2 className="mt-3 text-3xl font-bold tracking-tight text-zinc-50 sm:text-4xl">Less tab switching. More momentum.</h2>
              </div>
              <p className="max-w-md text-sm leading-6 text-zinc-400">Automate the repeatable parts of outreach while keeping every important reply close at hand.</p>
            </div>

            <div className="mt-12 grid gap-5 sm:grid-cols-2">
              {features.map((feature, index) => (
                <article key={feature.title} className="group rounded-2xl border border-surface-700 bg-surface-900 p-6 transition hover:border-accent-500/25 hover:bg-surface-850 sm:p-7">
                  <div className="flex items-start gap-4">
                    <span className={`grid h-12 w-12 shrink-0 place-items-center rounded-xl ${index === 1 ? 'bg-indigo-500/10 text-indigo-300' : index === 3 ? 'bg-emerald-500/10 text-emerald-300' : 'bg-accent-500/10 text-accent-300'}`}>
                      <FeatureIcon name={feature.icon} />
                    </span>
                    <div>
                      <h3 className="text-lg font-semibold text-zinc-100">{feature.title}</h3>
                      <p className="mt-2 max-w-lg text-sm leading-6 text-zinc-400">{feature.body}</p>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-7xl px-5 py-20 sm:px-6 lg:py-24">
          <div className="relative overflow-hidden rounded-3xl border border-accent-500/20 bg-[linear-gradient(125deg,rgba(20,184,166,0.13),rgba(22,22,26,0.9)_48%,rgba(99,102,241,0.11))] px-6 py-12 text-center sm:px-12 sm:py-16">
            <div className="pointer-events-none absolute left-1/2 top-0 h-56 w-96 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent-400/10 blur-3xl" />
            <div className="relative mx-auto max-w-2xl">
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent-300">Put your tools to work</p>
              <h2 className="mt-4 text-3xl font-bold tracking-tight text-white sm:text-4xl">Your next productive day can run itself.</h2>
              <p className="mx-auto mt-4 max-w-xl text-sm leading-6 text-zinc-400 sm:text-base">Connect your accounts, create your first campaign, and automate the tasks that slow you down.</p>
              <Link to={primaryPath} className="btn-primary mt-8 px-6 py-3.5 text-base">
                {primaryLabel}
                <ArrowIcon />
              </Link>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-white/[0.06]">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 px-5 py-8 text-sm text-zinc-500 sm:flex-row sm:px-6">
          <Link to="/" className="flex items-center gap-2 transition hover:text-zinc-300">
            <img src="/favicon.svg" alt="" className="h-5 w-5 opacity-70" />
            <span>LinkEasy</span>
          </Link>
          <p className="text-center sm:text-right">Connect your tools. Automate responsibly. Keep conversations human.</p>
        </div>
      </footer>
    </div>
  );
}
