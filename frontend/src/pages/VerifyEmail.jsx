import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { authApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { Spinner } from '../components/Spinner';

export default function VerifyEmail() {
  const location = useLocation();
  const navigate = useNavigate();
  const { email: storedEmail } = useAuth();

  const [email, setEmail] = useState(location.state?.email || storedEmail || '');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [resendCooldown, setResendCooldown] = useState(0);

  useEffect(() => {
    if (resendCooldown <= 0) return;
    const t = setTimeout(() => setResendCooldown((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [resendCooldown]);

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authApi.verifyEmail(email.trim(), code.trim());
      toast.success('Email verified — you can log in now.');
      navigate('/login');
    } catch (err) {
      setError(getErrorMessage(err, 'Verification failed — check the code.'));
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    try {
      await authApi.resendVerification(email.trim());
      toast.success('A fresh verification code is on its way.');
      setResendCooldown(30);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not resend the code.'));
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-950 bg-[radial-gradient(ellipse_at_top,rgba(45,212,191,0.07),transparent_55%)] px-4">
      <div className="w-full max-w-md">
        <Link to="/" className="mb-8 flex items-center justify-center gap-2.5">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <span className="text-xl font-bold tracking-tight">
            Reach<span className="text-accent-400">Pilot</span>
          </span>
        </Link>

        <div className="card animate-slide-up p-8">
          <h1 className="text-2xl font-bold text-zinc-50">Check your inbox</h1>
          <p className="mt-1 text-sm text-zinc-400">
            We sent a verification code to your email. Enter it below to activate your account.
          </p>

          <form onSubmit={onSubmit} className="mt-6 space-y-5">
            <div>
              <label htmlFor="verify-email" className="input-label">Email</label>
              <input
                id="verify-email"
                type="email"
                className="input-field"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div>
              <label htmlFor="verify-code" className="input-label">Verification code</label>
              <input
                id="verify-code"
                className="input-field text-center text-lg font-semibold tracking-[0.4em]"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="123456"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={10}
                required
              />
            </div>

            {error && (
              <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
                {error}
              </div>
            )}

            <button type="submit" className="btn-primary w-full" disabled={busy || !code.trim()}>
              {busy && <Spinner />}
              {busy ? 'Verifying…' : 'Verify email'}
            </button>
          </form>

          <div className="mt-5 text-center">
            <button
              onClick={resend}
              disabled={resendCooldown > 0 || !email.trim()}
              className="text-sm font-medium text-accent-400 transition hover:text-accent-300 disabled:cursor-not-allowed disabled:text-zinc-600"
            >
              {resendCooldown > 0 ? `Resend code in ${resendCooldown}s` : 'Resend verification code'}
            </button>
          </div>
        </div>

        <p className="mt-6 text-center text-sm text-zinc-500">
          Wrong address?{' '}
          <Link to="/signup" className="font-medium text-accent-400 hover:text-accent-300">
            Sign up again
          </Link>
        </p>
      </div>
    </div>
  );
}
