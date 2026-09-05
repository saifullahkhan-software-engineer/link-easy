import { Link } from 'react-router-dom';

export default function TermsOfService() {
  return (
    <div className="min-h-screen bg-surface-950 bg-[radial-gradient(ellipse_at_top,rgba(45,212,191,0.06),transparent_55%)] text-zinc-300">
      <header className="border-b border-surface-800/80 bg-surface-950/80 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-4">
          <Link to="/" className="flex items-center gap-2.5">
            <img src="/favicon.svg" alt="" className="h-7 w-7" />
            <span className="text-lg font-bold tracking-tight text-zinc-100">Link<span className="text-accent-400">Easy</span></span>
          </Link>
          <nav className="flex gap-4 text-sm text-zinc-400">
            <Link to="/privacy" className="hover:text-zinc-200">Privacy Policy</Link>
            <Link to="/" className="text-accent-400 hover:text-accent-300">Home</Link>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-12">
        <h1 className="text-3xl font-bold text-zinc-50">Terms of Service</h1>
        <p className="mt-2 text-sm text-zinc-500">Effective date: September 6, 2026</p>
        <div className="mt-8 space-y-6">
          <section className="card p-6"><h2 className="text-lg font-semibold text-zinc-100">1. The service</h2><p className="mt-2 text-sm leading-relaxed text-zinc-400">LinkEasy is a web service that helps users connect supported social accounts, manage content, schedule posts, and automate selected outreach workflows from one workspace.</p></section>
          <section className="card p-6"><h2 className="text-lg font-semibold text-zinc-100">2. Your account</h2><p className="mt-2 text-sm leading-relaxed text-zinc-400">You are responsible for providing accurate information, protecting your login credentials, and all activity performed through your account. You must be legally able to use the service and must not share access in a way that violates a connected platform’s rules.</p></section>
          <section className="card p-6"><h2 className="text-lg font-semibold text-zinc-100">3. Connected platforms</h2><p className="mt-2 text-sm leading-relaxed text-zinc-400">When you connect LinkedIn, WhatsApp, YouTube, Instagram, TikTok, Facebook, or another supported platform, you authorize LinkEasy to perform only the actions you select. Your use of each connected platform remains subject to that platform’s terms, policies, rate limits, and approval requirements. You can disconnect a platform from LinkEasy at any time.</p></section>
          <section className="card p-6"><h2 className="text-lg font-semibold text-zinc-100">4. Your content</h2><p className="mt-2 text-sm leading-relaxed text-zinc-400">You retain ownership of the videos, images, text, contact data, and other material you submit. You grant LinkEasy permission to store and process that material only as needed to provide the features you request, including uploading or publishing it to the connected platform you choose. You must have the rights and permissions required for all content and recipients you use.</p></section>
          <section className="card p-6"><h2 className="text-lg font-semibold text-zinc-100">5. Acceptable use</h2><p className="mt-2 text-sm leading-relaxed text-zinc-400">You must not use LinkEasy for unlawful, fraudulent, abusive, deceptive, discriminatory, or harmful activity; to send spam; to infringe another person’s rights; to bypass a platform’s security or limits; or to upload malicious code. We may suspend activity that creates a security, legal, or platform-compliance risk.</p></section>
          <section className="card p-6"><h2 className="text-lg font-semibold text-zinc-100">6. Availability and third-party services</h2><p className="mt-2 text-sm leading-relaxed text-zinc-400">We work to keep LinkEasy available, but features may be interrupted for maintenance, technical failures, or changes made by third-party platforms. We do not guarantee that a connected platform will approve, accept, publish, or retain a particular post.</p></section>
          <section className="card p-6"><h2 className="text-lg font-semibold text-zinc-100">7. Termination and deletion</h2><p className="mt-2 text-sm leading-relaxed text-zinc-400">You may stop using LinkEasy or request account deletion at any time through the <Link to="/delete" className="text-accent-400 hover:text-accent-300">data deletion page</Link>. We may suspend or terminate access when these terms are violated or when necessary to protect users, the service, or a third-party platform.</p></section>
          <section className="card p-6"><h2 className="text-lg font-semibold text-zinc-100">8. Disclaimer and liability</h2><p className="mt-2 text-sm leading-relaxed text-zinc-400">LinkEasy is provided on an “as available” basis. To the extent permitted by law, LinkEasy is not responsible for indirect losses, missed opportunities, platform outages, rejected content, account restrictions, or changes to third-party APIs resulting from your use of the service.</p></section>
          <section className="card p-6"><h2 className="text-lg font-semibold text-zinc-100">9. Changes and contact</h2><p className="mt-2 text-sm leading-relaxed text-zinc-400">We may update these terms when the service changes. The updated effective date will appear on this page. Questions about these terms can be sent to the support address shown in the service.</p></section>
        </div>
      </main>

      <footer className="border-t border-surface-800/80 py-6"><div className="mx-auto flex max-w-3xl items-center justify-between px-4 text-xs text-zinc-600"><span>© {new Date().getFullYear()} LinkEasy</span><span className="flex gap-4"><Link to="/privacy" className="hover:text-zinc-400">Privacy Policy</Link><Link to="/terms" className="hover:text-zinc-400">Terms of Service</Link><Link to="/delete" className="hover:text-zinc-400">Data Deletion</Link></span></div></footer>
    </div>
  );
}

// This page is public so platform reviewers can access it without signing in.
