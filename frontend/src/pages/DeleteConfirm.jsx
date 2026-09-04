import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { userDataApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';

const INVALID_MESSAGE =
  'This deletion link is invalid or has expired. Request a new one from the account deletion page.';

/**
 * Deletion confirmation page — served at /delete-confirm?token=…
 *
 * The one-time signed link from the deletion email lands here. On load the
 * page hands the token to the backend, which validates it and deletes the
 * account + all data (email-confirmed — this is the ONLY way an account is
 * deleted; a bare email never is). Public, no login required: by the time a
 * user clicks the link their session may be gone or never existed.
 */
export default function DeleteConfirm() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || '';
  const [state, setState] = useState(token ? 'confirming' : 'missing');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const started = useRef(false);

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await userDataApi.confirmDeletion(token);
        if (cancelled) return;
        setMessage(data?.message || 'Your account and all associated data have been deleted.');
        setState('done');
      } catch (err) {
        if (cancelled) return;
        const detail = getErrorMessage(err, INVALID_MESSAGE);
        setError(detail);
        setState('error');
        toast.error(detail);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-950 bg-[radial-gradient(ellipse_at_top,rgba(45,212,191,0.07),transparent_55%)] px-4">
      <div className="w-full max-w-lg">
        <Link to="/" className="mb-8 flex items-center justify-center gap-2.5">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <span className="text-xl font-bold tracking-tight text-zinc-100">
            Link<span className="text-accent-400">Easy</span>
          </span>
        </Link>

        <div className="card animate-slide-up p-8 text-center">
          {state === 'confirming' && (
            <>
              <div className="flex justify-center py-2">
                <Spinner />
              </div>
              <h1 className="mt-4 text-xl font-bold text-zinc-100">Deleting your account…</h1>
              <p className="mt-2 text-sm text-zinc-400">
                Please wait while we permanently remove your account and data.
              </p>
            </>
          )}

          {state === 'done' && (
            <>
              <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-emerald-500/15">
                <svg className="h-7 w-7 text-emerald-400" viewBox="0 0 20 20" fill="currentColor">
                  <path
                    fillRule="evenodd"
                    d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z"
                    clipRule="evenodd"
                  />
                </svg>
              </div>
              <h1 className="mt-4 text-xl font-bold text-emerald-300">Account deleted</h1>
              <p className="mt-2 text-sm leading-relaxed text-zinc-400">{message}</p>
              <p className="mt-1 text-xs text-zinc-500">
                All of your data has been permanently removed. Thanks for using LinkEasy.
              </p>
              <Link to="/" className="btn-primary mt-6 w-full">
                Back to home
              </Link>
            </>
          )}

          {state === 'missing' && (
            <>
              <h1 className="text-xl font-bold text-red-300">Invalid deletion link</h1>
              <p className="mt-2 text-sm text-zinc-400">
                This page needs a confirmation token from the account-deletion email.
              </p>
              <Link to="/delete" className="btn-primary mt-6 w-full">
                Request a new link
              </Link>
            </>
          )}

          {state === 'error' && (
            <>
              <h1 className="text-xl font-bold text-red-300">Deletion link failed</h1>
              <p className="mt-2 text-sm leading-relaxed text-zinc-400">{error}</p>
              <p className="mt-1 text-xs text-zinc-500">
                Deletion links are one-time and expire shortly after they are sent.
              </p>
              <Link to="/delete" className="btn-primary mt-6 w-full">
                Request a new link
              </Link>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
