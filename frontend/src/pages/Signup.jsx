import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { authApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { useAuth } from '../context/AuthContext';
import PasswordStrength, { passwordIsValid } from '../components/PasswordStrength';
import { Spinner } from '../components/Spinner';

export default function Signup() {
  const navigate = useNavigate();
  const { completeSignup } = useAuth();

  const [form, setForm] = useState({ first_name: '', last_name: '', email: '', password: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  async function onSubmit(e) {
    e.preventDefault();
    if (!passwordIsValid(form.password)) {
      setError('Please meet all password requirements below.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const payload = {
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        email: form.email.trim(),
        password: form.password,
      };
      const { data } = await authApi.register(payload);
      completeSignup(payload.email, `${payload.first_name} ${payload.last_name}`.trim());
      toast.success(data?.message || 'Account created — check your email for a verification code.');
      navigate('/verify-email', { state: { email: payload.email } });
    } catch (err) {
      setError(getErrorMessage(err, 'Registration failed.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-950 bg-[radial-gradient(ellipse_at_top,rgba(45,212,191,0.07),transparent_55%)] px-4 py-10">
      <div className="w-full max-w-md">
        <Link to="/" className="mb-8 flex items-center justify-center gap-2.5">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <span className="text-xl font-bold tracking-tight">
            Link<span className="text-accent-400">Easy</span>
          </span>
        </Link>

        <div className="card animate-slide-up p-8">
          <h1 className="text-2xl font-bold text-zinc-50">Create your account</h1>
          <p className="mt-1 text-sm text-zinc-400">Start automating outreach in minutes.</p>

          <form onSubmit={onSubmit} className="mt-6 space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="first_name" className="input-label">First name</label>
                <input id="first_name" className="input-field" value={form.first_name} onChange={set('first_name')} required autoComplete="given-name" />
              </div>
              <div>
                <label htmlFor="last_name" className="input-label">Last name</label>
                <input id="last_name" className="input-field" value={form.last_name} onChange={set('last_name')} required autoComplete="family-name" />
              </div>
            </div>
            <div>
              <label htmlFor="su-email" className="input-label">Email</label>
              <input id="su-email" type="email" className="input-field" value={form.email} onChange={set('email')} placeholder="you@company.com" required autoComplete="email" />
            </div>
            <div>
              <label htmlFor="su-password" className="input-label">Password</label>
              <input id="su-password" type="password" className="input-field" value={form.password} onChange={set('password')} placeholder="Create a strong password" required autoComplete="new-password" />
              <PasswordStrength password={form.password} />
            </div>

            {error && (
              <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
                {error}
              </div>
            )}

            <button type="submit" className="btn-primary w-full" disabled={busy}>
              {busy && <Spinner />}
              {busy ? 'Creating account…' : 'Sign up'}
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-sm text-zinc-500">
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-accent-400 hover:text-accent-300">
            Log in
          </Link>
        </p>
      </div>
    </div>
  );
}
