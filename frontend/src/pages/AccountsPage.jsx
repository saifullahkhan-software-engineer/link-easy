import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { linkedinApi, whatsappApi } from '../api/endpoints';
import { AccountStatusBadge } from '../components/Badge';
import WhatsAppStatusBadge from '../components/whatsapp/WhatsAppStatusBadge';
import { Spinner } from '../components/Spinner';

function NotConnectedBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 px-2.5 py-1 text-xs font-medium text-zinc-300 ring-1 ring-inset ring-zinc-500/20">
      <span className="h-1.5 w-1.5 rounded-full bg-zinc-400" />
      Not connected
    </span>
  );
}

/**
 * Accounts hub — one card per connection (LinkedIn + WhatsApp).
 * Each card shows the live status and links to its manage/connect page.
 */
export default function AccountsPage() {
  const [liLoading, setLiLoading] = useState(true);
  const [liAccount, setLiAccount] = useState(null);
  const [waLoading, setWaLoading] = useState(true);
  const [waStatus, setWaStatus] = useState('disconnected');

  const loadLinkedIn = useCallback(async () => {
    setLiLoading(true);
    try {
      const { data } = await linkedinApi.getAccount();
      setLiAccount(data);
    } catch {
      setLiAccount(null); // 404 or backend down → treat as not connected
    } finally {
      setLiLoading(false);
    }
  }, []);

  const loadWhatsApp = useCallback(async () => {
    setWaLoading(true);
    try {
      const { data } = await whatsappApi.getStatus();
      setWaStatus(data.status || 'disconnected');
    } catch {
      setWaStatus('disconnected');
    } finally {
      setWaLoading(false);
    }
  }, []);

  useEffect(() => {
    loadLinkedIn();
    loadWhatsApp();
  }, [loadLinkedIn, loadWhatsApp]);

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-bold text-zinc-50">Accounts</h1>
      <p className="mt-1 text-sm text-zinc-400">
        Manage the LinkedIn and WhatsApp connections your automations run from.
      </p>

      <div className="mt-6 grid grid-cols-1 gap-6 md:grid-cols-2">
        {/* ── LinkedIn card ─────────────────────────────────────── */}
        <div className="card flex flex-col p-6">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent-500/10 text-accent-300">
                <svg className="h-6 w-6" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M19 0h-14c-2.761 0-5 2.239-5 5v14c0 2.761 2.239 5 5 5h14c2.762 0 5-2.239 5-5v-14c0-2.761-2.238-5-5-5zm-11 19h-3v-11h3v11zm-1.5-12.268c-.966 0-1.75-.79-1.75-1.764s.784-1.764 1.75-1.764 1.75.79 1.75 1.764-.783 1.764-1.75 1.764zm13.5 12.268h-3v-5.604c0-3.368-4-3.113-4 0v5.604h-3v-11h3v1.765c1.396-2.586 7-2.777 7 2.476v6.759z" />
                </svg>
              </div>
              <div>
                <h2 className="text-lg font-semibold text-zinc-100">LinkedIn</h2>
                <p className="text-xs text-zinc-500">Campaigns &amp; outreach</p>
              </div>
            </div>
            {liLoading ? (
              <Spinner />
            ) : liAccount ? (
              <AccountStatusBadge status={liAccount.status} />
            ) : (
              <NotConnectedBadge />
            )}
          </div>

          <div className="mt-4 flex-1">
            {liLoading ? (
              <div className="animate-pulse space-y-2">
                <div className="h-4 w-48 rounded bg-surface-700" />
                <div className="h-3 w-32 rounded bg-surface-700" />
              </div>
            ) : liAccount ? (
              <>
                <p className="text-sm font-medium text-zinc-200">{liAccount.linkedin_email}</p>
                {liAccount.label && (
                  <p className="mt-0.5 text-sm text-zinc-500">{liAccount.label}</p>
                )}
                <p className="mt-2 text-xs leading-relaxed text-zinc-500">
                  Your campaigns run from this LinkedIn profile. Refresh or update its
                  credentials any time.
                </p>
              </>
            ) : (
              <p className="text-sm leading-relaxed text-zinc-500">
                No LinkedIn account connected yet. Connect one to create and run
                campaigns.
              </p>
            )}
          </div>

          <div className="mt-5 flex flex-wrap gap-3 border-t border-surface-700 pt-4">
            <Link to="/app/account/linkedin" className="btn-primary">
              {liAccount ? 'Manage LinkedIn account' : 'Connect LinkedIn account'}
            </Link>
          </div>
        </div>

        {/* ── WhatsApp card ─────────────────────────────────────── */}
        <div className="card flex flex-col p-6">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-green-500/10 text-green-300">
                <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 9.75a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375m-13.5 3.01c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 0 1 .778-.332 48.294 48.294 0 0 0 5.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z" />
                </svg>
              </div>
              <div>
                <h2 className="text-lg font-semibold text-zinc-100">WhatsApp</h2>
                <p className="text-xs text-zinc-500">Job scanner &amp; group monitoring</p>
              </div>
            </div>
            {waLoading ? <Spinner /> : <WhatsAppStatusBadge status={waStatus} />}
          </div>

          <div className="mt-4 flex-1">
            {waLoading ? (
              <div className="animate-pulse space-y-2">
                <div className="h-4 w-40 rounded bg-surface-700" />
                <div className="h-3 w-56 rounded bg-surface-700" />
              </div>
            ) : waStatus === 'connected' ? (
              <p className="text-sm leading-relaxed text-zinc-500">
                WhatsApp is connected and ready — the job scanner can monitor your
                groups and forward matches.
              </p>
            ) : waStatus === 'waiting_qr' ? (
              <p className="text-sm leading-relaxed text-yellow-400/90">
                A connection is in progress — finish scanning the QR code to link
                WhatsApp.
              </p>
            ) : (
              <p className="text-sm leading-relaxed text-zinc-500">
                Not connected. Link WhatsApp by scanning a QR code to enable the job
                scanner.
              </p>
            )}
          </div>

          <div className="mt-5 flex flex-wrap gap-3 border-t border-surface-700 pt-4">
            <Link to="/app/account/whatsapp" className="btn-primary">
              {waStatus === 'connected' ? 'Manage WhatsApp connection' : 'Connect WhatsApp'}
            </Link>
            {waStatus === 'connected' && (
              <Link to="/app/whatsapp-scanner" className="btn-secondary">
                Open scanner →
              </Link>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
