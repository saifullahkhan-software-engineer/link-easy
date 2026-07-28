import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { linkedinApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import Modal from './Modal';
import { SlowOperationNotice, Spinner } from './Spinner';

/**
 * Shown whenever LinkedIn asks for a 2FA / checkpoint code — both during
 * the initial account connect and during a session refresh. Submits to
 * POST /linkedin/account/verify and reports LOGIN_SUCCESS or
 * VERIFICATION_FAILED back through onResolved.
 */
export default function VerificationCodeModal({ open, sessionId, onClose, onResolved }) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef(null);

  useEffect(() => {
    if (open) {
      setCode('');
      setError(null);
      setBusy(false);
      setElapsed(0);
    }
  }, [open, sessionId]);

  useEffect(() => {
    if (busy) {
      timerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
      return () => clearInterval(timerRef.current);
    }
  }, [busy]);

  async function submit(e) {
    e?.preventDefault();
    const trimmed = code.trim();
    if (trimmed.length < 4) {
      setError('Enter the verification code LinkedIn sent you (4–10 characters).');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { data } = await linkedinApi.submitVerificationCode(sessionId, trimmed);
      if (data.status === 'LOGIN_SUCCESS') {
        toast.success('Verified — LinkedIn account connected.');
        onResolved?.('LOGIN_SUCCESS', data);
        onClose?.();
      } else {
        // VERIFICATION_FAILED — keep modal open, let the user retry.
        setError(data.message || 'Verification failed. Check the code and try again.');
        onResolved?.('VERIFICATION_FAILED', data);
      }
    } catch (err) {
      setError(getErrorMessage(err, 'Could not submit the verification code.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={busy ? undefined : onClose} title="LinkedIn verification required">
      <p className="mb-4 text-sm text-zinc-400">
        LinkedIn is asking for a verification code before it lets this login through. Check the
        email or device linked to the LinkedIn account and enter the code below.
      </p>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label htmlFor="verification-code" className="input-label">
            Verification code
          </label>
          <input
            id="verification-code"
            className="input-field text-center text-lg font-semibold tracking-[0.4em]"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="123456"
            autoComplete="one-time-code"
            inputMode="numeric"
            maxLength={10}
            disabled={busy}
            autoFocus
          />
        </div>

        {busy && (
          <SlowOperationNotice
            title="Submitting code to LinkedIn…"
            hint="A real browser session is verifying your code — this can take up to 30 seconds. Don't close this window."
            elapsedSeconds={elapsed}
          />
        )}

        {error && !busy && (
          <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-3">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={busy || code.trim().length < 4}>
            {busy && <Spinner />}
            {busy ? 'Verifying…' : 'Submit code'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
