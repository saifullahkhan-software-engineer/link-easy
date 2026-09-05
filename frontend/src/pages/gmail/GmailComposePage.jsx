import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { gmailApi } from '../../api/gmail';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../../components/Spinner';
import ComposeForm from '../../components/gmail/ComposeForm';
import { GmailMark } from '../../components/gmail/GmailBits';

/**
 * Standalone compose page (/app/gmail/compose). Replying inside a
 * conversation uses the same ComposeForm in a modal on the inbox page.
 */
export default function GmailComposePage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const { data } = await gmailApi.status();
        if (active) setStatus(data);
      } catch (err) {
        if (active) toast.error(getErrorMessage(err, 'Could not load the Gmail connection'));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center gap-3 text-zinc-400">
        <Spinner className="h-5 w-5" /> Loading Gmail…
      </div>
    );
  }

  if (!status?.connected) {
    return (
      <div className="card mx-auto mt-8 max-w-lg p-8 text-center">
        <div className="flex justify-center text-rose-300">
          <GmailMark className="h-10 w-10" />
        </div>
        <h1 className="mt-4 text-xl font-semibold text-zinc-100">Connect Gmail first</h1>
        <p className="mt-2 text-sm text-zinc-400">
          You need a connected mailbox before you can compose. Head to the Gmail inbox to connect
          it.
        </p>
        <Link to="/app/gmail" className="btn-primary mt-5">
          Go to Gmail inbox
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-4 flex items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-rose-500/10 text-rose-300">
          <GmailMark className="h-6 w-6" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-zinc-50">Compose</h1>
          <p className="text-sm text-zinc-400">
            Sending as <span className="text-zinc-200">{status.account_email}</span>
          </p>
        </div>
      </div>

      <div className="card p-6">
        <ComposeForm
          onSent={() => navigate('/app/gmail')}
          onCancel={() => navigate('/app/gmail')}
        />
      </div>
      <p className="mt-3 text-xs text-zinc-500">
        Sent from your own mailbox through Gmail's API — subject to your account's regular sending
        limits. LinkEasy is not for bulk cold email.
      </p>
    </div>
  );
}
