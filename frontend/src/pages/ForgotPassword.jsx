import { useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { authApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';

export default function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await authApi.forgotPassword(email.trim());
      setSent(true);
      toast.success('If that email is registered, a reset link is on its way.');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not send reset email.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-950 bg-[radial-gradient(ellipse_at_top,rgba(45,212,191,0.07),transparent_55%)] px-4">
      <div className="w-full max-w-md">
        <Link to="/" className="mb-8 flex items-center justify-center gap-2.5">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <span className="text-xl font-bold tracking-tight">
            Link<span className="text-accent-400">Easy</span>
          </span>
        </Link>

        <div className="card animate-slide-up p-8">
          <h1 className="text-2xl font-bold text-zinc-50">Reset your password</h1>
          <p className="mt-1 text-sm text-zinc-400">
            {sent
              ? 'Check your email for a password reset link.'
              : 'Enter your account email and we will send you a reset link.'}
          </p>

          {!sent && (
            <form onSubmit={onSubmit} className="mt-6 space-y-5">
              <div>
                <label htmlFor="fp-email" className="input-label">Email</label>
                <input
                  id="fp-email"
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
                {busy ? 'Sending…' : 'Send reset link'}
              </button>
            </form>
          )}

          <p className="mt-6 text-center text-sm text-zinc-500">
            Remembered it after all?{' '}
            <Link to="/login" className="font-medium text-accent-400 hover:text-accent-300">
              Back to login
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
