import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useAuth } from '../context/AuthContext';
import { authApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = location.state?.from || '/app';

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      toast.success('Welcome back');
      navigate(from, { replace: true });
    } catch (err) {
      // 403 = account exists but the email was never verified. Send a fresh
      // code and drop the user on the verification page instead of just
      // showing an error.
      if (err?.response?.status === 403) {
        try {
          await authApi.resendVerification(email.trim());
          toast.success('A new verification code was sent to your email.');
        } catch (resendErr) {
          toast.error(
            getErrorMessage(resendErr, 'Could not send a new code — use "Resend" on the next page.')
          );
        }
        navigate('/verify-email', { state: { email: email.trim(), codeSent: true } });
        return;
      }
      setError(getErrorMessage(err, 'Login failed — check your credentials.'));
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
          <h1 className="text-2xl font-bold text-zinc-50">Log in</h1>
          <p className="mt-1 text-sm text-zinc-400">Welcome back — your pipeline missed you.</p>

          <form onSubmit={onSubmit} className="mt-6 space-y-5">
            <div>
              <label htmlFor="email" className="input-label">Email</label>
              <input
                id="email"
                type="email"
                className="input-field"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                required
                autoComplete="email"
              />
            </div>
            <div>
              <div className="flex items-center justify-between">
                <label htmlFor="password" className="input-label mb-0">Password</label>
                <Link to="/forgot-password" className="text-xs font-medium text-accent-400 hover:text-accent-300">
                  Forgot password?
                </Link>
              </div>
              <input
                id="password"
                type="password"
                className="input-field mt-1.5"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
                autoComplete="current-password"
              />
            </div>

            {error && (
              <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
                {error}
              </div>
            )}

            <button type="submit" className="btn-primary w-full" disabled={busy}>
              {busy && <Spinner />}
              {busy ? 'Logging in…' : 'Log in'}
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-sm text-zinc-500">
          New here?{' '}
          <Link to="/signup" className="font-medium text-accent-400 hover:text-accent-300">
            Create an account
          </Link>
        </p>
      </div>
    </div>
  );
}
