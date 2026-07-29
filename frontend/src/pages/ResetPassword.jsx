import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { authApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import PasswordStrength, { passwordIsValid } from '../components/PasswordStrength';
import { Spinner } from '../components/Spinner';

export default function ResetPassword() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get('token') || '';

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function onSubmit(e) {
    e.preventDefault();
    if (!passwordIsValid(password)) {
      setError('Please meet all password requirements below.');
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await authApi.resetPassword(token, password);
      toast.success('Password updated — log in with your new password.');
      navigate('/login');
    } catch (err) {
      setError(getErrorMessage(err, 'Reset failed — the link may have expired.'));
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-950 px-4">
        <div className="card w-full max-w-md p-8 text-center">
          <h1 className="text-xl font-bold text-red-300">Invalid reset link</h1>
          <p className="mt-2 text-sm text-zinc-400">This link is missing a reset token.</p>
          <Link to="/forgot-password" className="btn-primary mt-6 w-full">
            Request a new link
          </Link>
        </div>
      </div>
    );
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
          <h1 className="text-2xl font-bold text-zinc-50">Choose a new password</h1>

          <form onSubmit={onSubmit} className="mt-6 space-y-5">
            <div>
              <label htmlFor="rp-password" className="input-label">New password</label>
              <input
                id="rp-password"
                type="password"
                className="input-field"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="new-password"
              />
              <PasswordStrength password={password} />
            </div>
            <div>
              <label htmlFor="rp-confirm" className="input-label">Confirm password</label>
              <input
                id="rp-confirm"
                type="password"
                className="input-field"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
                autoComplete="new-password"
              />
              {confirm && password !== confirm && (
                <p className="mt-1 text-xs text-red-400">Passwords do not match.</p>
              )}
            </div>

            {error && (
              <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
                {error}
              </div>
            )}

            <button type="submit" className="btn-primary w-full" disabled={busy}>
              {busy && <Spinner />}
              {busy ? 'Updating…' : 'Update password'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
