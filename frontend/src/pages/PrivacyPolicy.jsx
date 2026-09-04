import { Link } from 'react-router-dom';

/**
 * Static privacy policy — served at /privacy (public, no login required).
 *
 * This page exists so the deployment can fill Meta app Settings → Basic →
 * "Privacy Policy URL" (https://<domain>/privacy). It must never require
 * authentication: Meta's review crawler and users clicking the link from the
 * Facebook/Instagram consent screen are not logged in.
 */
export default function PrivacyPolicy() {
  return (
    <div className="min-h-screen bg-surface-950 bg-[radial-gradient(ellipse_at_top,rgba(45,212,191,0.06),transparent_55%)] text-zinc-300">
      <header className="border-b border-surface-800/80 bg-surface-950/80 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-4">
          <Link to="/" className="flex items-center gap-2.5">
            <img src="/favicon.svg" alt="" className="h-7 w-7" />
            <span className="text-lg font-bold tracking-tight text-zinc-100">
              Link<span className="text-accent-400">Easy</span>
            </span>
          </Link>
          <nav className="flex items-center gap-4 text-sm text-zinc-400">
            <Link to="/delete" className="hover:text-zinc-200">
              Data deletion
            </Link>
            <Link to="/" className="text-accent-400 hover:text-accent-300">
              Home
            </Link>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-12">
        <h1 className="text-3xl font-bold text-zinc-50">Privacy Policy</h1>
        <p className="mt-2 text-sm text-zinc-500">
          Effective date: September 4, 2026
        </p>

        <div className="prose-sm mt-8 space-y-8">
          <section className="card p-6">
            <h2 className="text-lg font-semibold text-zinc-100">1. What this policy covers</h2>
            <p className="mt-2 text-sm leading-relaxed text-zinc-400">
              This policy explains what LinkEasy (“we”, “the service”) collects and stores when you use it. LinkEasy
              lets you schedule and publish content to LinkedIn, WhatsApp, YouTube, Instagram, TikTok and Facebook from
              one place, and automates routine actions on your own accounts.
            </p>
          </section>

          <section className="card p-6">
            <h2 className="text-lg font-semibold text-zinc-100">2. Information you give us</h2>
            <ul className="mt-2 list-disc space-y-2 pl-5 text-sm leading-relaxed text-zinc-400">
              <li>
                <strong className="text-zinc-300">Account details</strong> — your name, email address and a password
                (stored only as a salted, hashed value; we never see or store the plaintext password).
              </li>
              <li>
                <strong className="text-zinc-300">Connected-account credentials</strong> — when you connect LinkedIn,
                WhatsApp or a social platform, the access tokens those platforms issue. They are encrypted at rest and
                used only to perform the actions you schedule.
              </li>
              <li>
                <strong className="text-zinc-300">Content you create</strong> — scheduled posts, captions, uploaded
                videos, lead lists and automation settings.
              </li>
              <li>
                <strong className="text-zinc-300">Automation activity</strong> — logs of what the automation did on
                your behalf (e.g. which profiles it visited or messaged), so you can audit it.
              </li>
            </ul>
          </section>

          <section className="card p-6">
            <h2 className="text-lg font-semibold text-zinc-100">3. How we use your information</h2>
            <p className="mt-2 text-sm leading-relaxed text-zinc-400">
              We use the information above only to operate the service: to keep you signed in, run your scheduled and
              recurring automation jobs, publish your posts, show you status and history, and communicate with you
              about your account (for example verification, password-reset and account-deletion emails). We do not sell
              your personal information.
            </p>
          </section>

          <section className="card p-6">
            <h2 className="text-lg font-semibold text-zinc-100">4. Information shared with the platforms</h2>
            <p className="mt-2 text-sm leading-relaxed text-zinc-400">
              When you connect a third-party platform (LinkedIn, WhatsApp, YouTube, Instagram, TikTok or Facebook), the
              actions you configure are performed through that platform’s own API using your own account. Content you
              schedule is delivered to the platform you selected. Each platform’s own privacy policy applies to that
              platform.
            </p>
          </section>

          <section className="card p-6">
            <h2 className="text-lg font-semibold text-zinc-100">5. How long we keep your information</h2>
            <p className="mt-2 text-sm leading-relaxed text-zinc-400">
              We keep your information for as long as your account is active, so your scheduled jobs and history keep
              working. When you delete your account, the information above is deleted — see section 6.
            </p>
          </section>

          <section className="card p-6">
            <h2 className="text-lg font-semibold text-zinc-100">6. Deleting your data</h2>
            <p className="mt-2 text-sm leading-relaxed text-zinc-400">
              You can delete your account and all of the data we hold for it at any time. Deletion is email-confirmed
              for your safety: visit the{' '}
              <Link to="/delete" className="font-medium text-accent-400 hover:text-accent-300">
                account deletion page
              </Link>
              , enter your account email, and click the one-time confirmation link we send you. Once confirmed, your
              account, connected platforms, scheduled posts, uploads, automation history and related data are
              permanently removed.
            </p>
          </section>

          <section className="card p-6">
            <h2 className="text-lg font-semibold text-zinc-100">7. Data security</h2>
            <p className="mt-2 text-sm leading-relaxed text-zinc-400">
              Passwords are stored as salted hashes and platform access tokens are encrypted at rest. Communications
              with the service are encrypted in transit. Access to the service is limited to your own session.
            </p>
          </section>

          <section className="card p-6">
            <h2 className="text-lg font-semibold text-zinc-100">8. Contact</h2>
            <p className="mt-2 text-sm leading-relaxed text-zinc-400">
              Questions about this policy or about your data can be sent to the support address shown on the service.
              You can also use the{' '}
              <Link to="/delete" className="font-medium text-accent-400 hover:text-accent-300">
                account deletion page
              </Link>{' '}
              to remove your data at any time.
            </p>
          </section>
        </div>
      </main>

      <footer className="border-t border-surface-800/80 py-6">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 text-xs text-zinc-600">
          <span>© {new Date().getFullYear()} LinkEasy</span>
          <span className="flex gap-4">
            <Link to="/privacy" className="hover:text-zinc-400">
              Privacy Policy
            </Link>
            <Link to="/delete" className="hover:text-zinc-400">
              Data Deletion
            </Link>
          </span>
        </div>
      </footer>
    </div>
  );
}
