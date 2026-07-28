import { Suspense, lazy, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

// three.js is heavy — only pull it in when the landing hero actually renders,
// never for dashboard/auth routes.
const NetworkHero = lazy(() => import('../components/NetworkHero'));

const features = [
  {
    title: 'Automated connection requests',
    body: 'Queue personalized connection notes at scale — with daily caps that keep your account under the radar.',
    icon: (
      <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path strokeLinecap="round" strokeLinejoin="round" d="M18 7.5v3m0 0v3m0-3h3m-3 0h-3m-2.25-4.125a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0ZM3 19.235v-.11a6.375 6.375 0 0 1 12.75 0v.109A12.318 12.318 0 0 1 9.374 21c-2.331 0-4.512-.645-6.374-1.766Z" />
      </svg>
    ),
  },
  {
    title: 'Drip messaging sequences',
    body: 'Multi-step follow-ups triggered on acceptance. Templates with {{first_name}} personalization keep it human.',
    icon: (
      <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm3.75 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm3.75 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0ZM21 12c0 4.556-4.03 8.25-9 8.25a9.76 9.76 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
      </svg>
    ),
  },
  {
    title: 'CSV lead import',
    body: 'Drop in a CSV of prospects. Strict validation catches bad rows before a single lead is committed.',
    icon: (
      <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m6.75 12-3-3m0 0-3 3m3-3v6m-1.5-15H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
      </svg>
    ),
  },
  {
    title: 'Safe, human-like pacing',
    body: 'Randomized delays, staggered scheduling, and conservative daily limits protect your LinkedIn account.',
    icon: (
      <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z" />
      </svg>
    ),
  },
];

/** CSS-3D tilted dashboard mockup floating in the hero. */
function TiltedMockup() {
  return (
    <div className="pointer-events-none select-none" style={{ perspective: '1200px' }}>
      <div
        className="card w-[420px] p-4 opacity-90 shadow-2xl shadow-accent-500/10"
        style={{ transform: 'rotateY(-18deg) rotateX(8deg) rotateZ(2deg)' }}
      >
        <div className="mb-3 flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-zinc-600" />
          <span className="h-2.5 w-2.5 rounded-full bg-zinc-600" />
          <span className="h-2.5 w-2.5 rounded-full bg-accent-500" />
        </div>
        <div className="space-y-2">
          <div className="flex items-center justify-between rounded-md bg-surface-800 px-3 py-2">
            <div className="h-2 w-24 rounded bg-zinc-600" />
            <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
              replied
            </span>
          </div>
          <div className="flex items-center justify-between rounded-md bg-surface-800 px-3 py-2">
            <div className="h-2 w-32 rounded bg-zinc-600" />
            <span className="rounded-full bg-purple-500/15 px-2 py-0.5 text-[10px] font-medium text-purple-300">
              messaged
            </span>
          </div>
          <div className="flex items-center justify-between rounded-md bg-surface-800 px-3 py-2">
            <div className="h-2 w-20 rounded bg-zinc-600" />
            <span className="rounded-full bg-indigo-500/15 px-2 py-0.5 text-[10px] font-medium text-indigo-300">
              requested
            </span>
          </div>
          <div className="flex items-center justify-between rounded-md bg-surface-800 px-3 py-2">
            <div className="h-2 w-28 rounded bg-zinc-600" />
            <span className="rounded-full bg-zinc-500/15 px-2 py-0.5 text-[10px] font-medium text-zinc-400">
              pending
            </span>
          </div>
        </div>
        <div className="mt-3 flex gap-2">
          <div className="h-7 w-24 rounded-md bg-accent-500/80" />
          <div className="h-7 w-24 rounded-md bg-surface-700" />
        </div>
      </div>
    </div>
  );
}

export default function Landing() {
  const { isAuthenticated } = useAuth();
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  );

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReducedMotion(mq.matches);
    const onChange = (e) => setReducedMotion(e.matches);
    mq.addEventListener?.('change', onChange);
    return () => mq.removeEventListener?.('change', onChange);
  }, []);

  return (
    <div className="min-h-screen bg-surface-950">
      {/* Nav */}
      <header className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
        <div className="flex items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="text-lg font-bold tracking-tight">
            Reach<span className="text-accent-400">Pilot</span>
          </span>
        </div>
        <nav className="flex items-center gap-3">
          {isAuthenticated ? (
            <Link to="/app" className="btn-primary">
              Open dashboard
            </Link>
          ) : (
            <>
              <Link to="/login" className="btn-secondary">
                Log in
              </Link>
              <Link to="/signup" className="btn-primary">
                Get Started
              </Link>
            </>
          )}
        </nav>
      </header>

      {/* Hero */}
      <section className="relative mx-auto grid max-w-7xl grid-cols-1 items-center gap-8 px-6 pb-24 pt-12 lg:grid-cols-2 lg:pt-20">
        <div className="relative z-10">
          <p className="mb-4 inline-flex items-center gap-2 rounded-full border border-accent-500/30 bg-accent-500/10 px-3 py-1 text-xs font-medium text-accent-300">
            <span className="h-1.5 w-1.5 rounded-full bg-accent-400" />
            Outreach automation for LinkedIn
          </p>
          <h1 className="text-4xl font-extrabold leading-[1.1] tracking-tight text-zinc-50 sm:text-5xl lg:text-6xl">
            Turn cold profiles into{' '}
            <span className="bg-gradient-to-r from-accent-300 to-accent-500 bg-clip-text text-transparent">
              warm conversations
            </span>
          </h1>
          <p className="mt-6 max-w-lg text-lg leading-relaxed text-zinc-400">
            ReachPilot automates connection requests, follow-up sequences, and profile visits with
            conservative, human-like pacing — so you fill your pipeline without risking your account.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-4">
            <Link to="/signup" className="btn-primary px-6 py-3 text-base">
              Get Started — free
            </Link>
            <Link to="/login" className="text-sm font-medium text-zinc-400 transition hover:text-zinc-200">
              Already have an account →
            </Link>
          </div>
          <dl className="mt-12 flex gap-10">
            {[
              ['15/day', 'connection cap'],
              ['3-step', 'drip sequences'],
              ['100%', 'row-level CSV checks'],
            ].map(([stat, label]) => (
              <div key={label}>
                <dt className="text-2xl font-bold text-accent-300">{stat}</dt>
                <dd className="mt-1 text-xs uppercase tracking-wide text-zinc-500">{label}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="relative h-[420px] lg:h-[520px]">
          {reducedMotion ? (
            // Static fallback for reduced-motion / low-end devices.
            <div className="absolute inset-0 rounded-3xl bg-[radial-gradient(ellipse_at_center,rgba(45,212,191,0.18),transparent_65%)]" />
          ) : (
            <Suspense
              fallback={
                <div className="absolute inset-0 rounded-3xl bg-[radial-gradient(ellipse_at_center,rgba(45,212,191,0.12),transparent_65%)]" />
              }
            >
              <NetworkHero className="absolute inset-0" />
            </Suspense>
          )}
          <div className="absolute -bottom-6 right-0 hidden xl:block">
            <TiltedMockup />
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="border-t border-surface-800 bg-surface-900/50">
        <div className="mx-auto max-w-7xl px-6 py-20">
          <h2 className="text-center text-3xl font-bold tracking-tight text-zinc-50">
            Everything you need to run outreach on rails
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-center text-zinc-400">
            Built for operators who care about deliverability and account safety as much as volume.
          </p>
          <div className="mt-12 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {features.map((f) => (
              <div key={f.title} className="card p-6 transition hover:border-accent-500/30">
                <div className="mb-4 inline-flex rounded-lg bg-accent-500/10 p-2.5 text-accent-400">
                  {f.icon}
                </div>
                <h3 className="text-base font-semibold text-zinc-100">{f.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-zinc-400">{f.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-surface-800">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 px-6 py-8 text-sm text-zinc-500 sm:flex-row">
          <div className="flex items-center gap-2">
            <img src="/favicon.svg" alt="" className="h-5 w-5 opacity-70" />
            <span>ReachPilot</span>
          </div>
          <p>Automate responsibly. Respect LinkedIn&apos;s terms and your prospects&apos; time.</p>
        </div>
      </footer>
    </div>
  );
}
