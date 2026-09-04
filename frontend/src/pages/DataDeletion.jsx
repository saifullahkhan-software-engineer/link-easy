import { useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { userDataApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';

/**
 * Account deletion request page — served at /delete (public, no login).
 *
 * This is the "User Data Deletion URL" Meta's app review asks for. Meta only
 * requires that a user can *request* deletion here and that it is confirmed;
 * we go further and make the confirmation email-based: entering an email
 * never deletes anything — it sends a one-time signed confirmation link.
 * The response is identical whether or not the account exists, so this page
 * cannot be used to probe for registered accounts.
 */
export default function DataDeletion() {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await userDataApi.requestDeletion(email.trim());
      setSent(true);
      toast.success('Check your email for the confirmation link.');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not send the confirmation email.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-950 bg-[radial-gradient(ellipse_at_top,rgba(45,212,191,0.07),transparent_55%)] px-4">
      <div className="w-full max-w-lg">
        <Link to="/" className="mb-8 flex items-center justify-center gap-2.5">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <span className="text-xl font-bold tracking-tight text-zinc-100">
            Link<span className="text-accent-400">Easy</span>
          </span>
        </Link>

        <div className="card animate-slide-up p-8">
          <h1 className="text-2xl font-bold text-zinc-50">Delete your account</h1>
          <p className="mt-1 text-sm text-zinc-400">
            {sent
              ? 'One more step — open the confirmation link we emailed you.'
              : 'Enter the email address of the account you want to delete.'}
          </p>

          {sent ? (
            <div className="mt-6 space-y-4">
              <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-4 text-sm text-emerald-200">
                If an account exists for this email, a confirmation link with instructions is on its way. Check your
                inbox (and spam folder). Clicking the link permanently deletes the account and all of its data — it
                cannot be undone.
              </div>
              <Link to="/" className="btn-secondary w-full justify-center">
                Back to home
              </Link>
            </div>
          ) : (
            <>
              <form onSubmit={onSubmit} className="mt-6 space-y-5">
                <div>
                  <label htmlFor="delete-email" className="input-label">
                    Email
                  </label>
                  <input
                    id="delete-email"
                    type="email"
                    className="input-field"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@company.com"
                    required
                  />
                </div>
                <button type="submit" className="btn-primary w-full" disabled={busy}>
                  {busy && <Spinner />}
                  {busy ? 'Sending…' : 'Send confirmation link'}
                </button>
              </form>

              <div className="mt-6 rounded-lg border border-surface-700 bg-surface-800/60 p-4 text-xs leading-relaxed text-zinc-400">
                <strong className="text-zinc-300">Nothing is deleted yet.</strong> Sending this form emails you a
                one-time confirmation link. Your account and all of its data — connected platforms, scheduled posts,
                uploads, and automation history — are deleted only after you click that link. Deletion cannot be
                undone.
              </div>
            </>
          )}

          <p className="mt-6 text-center text-sm text-zinc-500">
            Changed your mind?{' '}
            <Link to="/" className="font-medium text-accent-400 hover:text-accent-300">
              Back to home
            </Link>
          </p>
        </div>

        <p className="mt-6 text-center text-xs text-zinc-600">
          Questions about your data? See the{' '}
          <Link to="/privacy" className="text-zinc-400 hover:text-zinc-300">
            Privacy Policy
          </Link>
          .
        </p>
      </div>
    </div>
  );
}
