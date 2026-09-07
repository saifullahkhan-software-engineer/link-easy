import { useCallback, useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { linkedinApi, whatsappApi } from '../api/endpoints';
import { gmailApi } from '../api/gmail';
import { AccountStatusBadge } from '../components/Badge';
import WhatsAppStatusBadge from '../components/whatsapp/WhatsAppStatusBadge';
import { GmailStatusBadge } from '../components/gmail/GmailBits';
import SocialConnectionsSection from '../components/accounts/SocialConnectionsSection';

function NotConnectedBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-500/10 px-2.5 py-1 text-xs font-medium text-zinc-300 ring-1 ring-inset ring-zinc-500/20">
      <span className="h-1.5 w-1.5 rounded-full bg-zinc-400" />
      Not connected
    </span>
  );
}

/* Platform glyphs kept local so the Accounts hub stays self-contained. */
function WhatsAppIcon() {
  return (
    <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 9.75a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375m-13.5 3.01c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 0 1 .778-.332 48.294 48.294 0 0 0 5.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z" />
    </svg>
  );
}
function LinkedInIcon() {
  return (
    <svg className="h-6 w-6" viewBox="0 0 24 24" fill="currentColor">
      <path d="M19 0h-14c-2.761 0-5 2.239-5 5v14c0 2.761 2.239 5 5 5h14c2.762 0 5-2.239 5-5v-14c0-2.761-2.238-5-5-5zm-11 19h-3v-11h3v11zm-1.5-12.268c-.966 0-1.75-.79-1.75-1.764s.784-1.764 1.75-1.764 1.75.79 1.75 1.764-.783 1.764-1.75 1.764zm13.5 12.268h-3v-5.604c0-3.368-4-3.113-4 0v5.604h-3v-11h3v1.765c1.396-2.586 7-2.777 7 2.476v6.759z" />
    </svg>
  );
}
function GmailIcon() {
  return (
    <svg className="h-6 w-6" viewBox="0 0 24 24" fill="currentColor">
      <path d="M1.5 5.25A2.25 2.25 0 0 1 3.75 3h16.5a2.25 2.25 0 0 1 2.25 2.25v13.5A2.25 2.25 0 0 1 20.25 21H3.75a2.25 2.25 0 0 1-2.25-2.25V5.25Zm1.5.66v12.84c0 .41.34.75.75.75h16.5c.41 0 .75-.34.75-.75V5.91l-8.28 6.07a1.5 1.5 0 0 1-1.68 0L3 5.91Zm1.03-.66L12 11.32l7.97-6.07H4.03Z" />
    </svg>
  );
}

/** A group of connection cards for one platform, with a connect/manage header. */
function PlatformGroup({ icon, title, to, connectLabel, children }) {
  return (
    <div className="mt-7">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-surface-800 text-zinc-200">{icon}</div>
          <h3 className="text-lg font-semibold text-zinc-100">{title}</h3>
        </div>
        <Link to={to} className="btn-secondary text-xs">{connectLabel}</Link>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">{children}</div>
    </div>
  );
}

/** One card per connected account/session/mailbox. */
function ConnectionCard({ icon, title, subtitle, badge, manageTo, manageLabel }) {
  return (
    <div className="card relative flex min-w-0 flex-col p-5">
      <div className="flex items-start gap-3 pb-5 pr-28">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-surface-800 text-zinc-200">{icon}</div>
          <div className="min-w-0">
            <h3 className="text-lg font-semibold text-zinc-100">{title}</h3>
            {subtitle ? (
              <p className="mt-0.5 truncate text-sm text-zinc-400">{subtitle}</p>
            ) : (
              <p className="mt-0.5 h-4 w-28 animate-pulse rounded bg-surface-700" />
            )}
          </div>
        </div>
        {badge && <div className="absolute right-5 top-5">{badge}</div>}
      </div>
      <div className="mt-auto flex flex-wrap gap-3 border-t border-surface-700 pt-4">
        <Link to={manageTo} className="btn-primary">{manageLabel}</Link>
      </div>
    </div>
  );
}

function EmptyConnectionCard({ icon, label, to, label2 }) {
  return (
    <div className="card flex min-w-0 flex-col p-5">
      <div className="flex min-w-0 items-center gap-3 pb-5">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-surface-800 text-zinc-200">{icon}</div>
        <p className="text-sm text-zinc-400">{label}</p>
      </div>
      <div className="mt-auto border-t border-surface-700 pt-4">
        {label2 ? <p className="text-xs text-zinc-500">{label2}</p> : <Link to={to} className="btn-secondary text-xs">Connect</Link>}
      </div>
    </div>
  );
}

/**
 * Accounts hub — Main accounts (WhatsApp, LinkedIn, Gmail) and Socials.
 *
 * Each card only shows the account and its status. All details — when it was
 * added, session health, disconnect, and the Scan / Live Chat shortcuts —
 * live on each account's manage page.
 */
export default function AccountsPage() {
  const { hash, key } = useLocation();

  useEffect(() => {
    if (hash === '#socials' || hash === '#main-accounts') {
      document.getElementById(hash.slice(1))?.scrollIntoView({ block: 'start' });
    }
  }, [hash, key]);
  // WhatsApp — one card per connected device/session.
  const [waLoading, setWaLoading] = useState(true);
  const [waSessions, setWaSessions] = useState([]);
  // LinkedIn — one card per connected profile.
  const [liLoading, setLiLoading] = useState(true);
  const [liAccounts, setLiAccounts] = useState([]);
  // Gmail — one card per connected mailbox.
  const [gmLoading, setGmLoading] = useState(true);
  const [gmAccounts, setGmAccounts] = useState([]);

  const loadWhatsApp = useCallback(async () => {
    setWaLoading(true);
    try {
      const { data } = await whatsappApi.listSessions();
      setWaSessions(Array.isArray(data?.sessions) ? data.sessions : []);
    } catch {
      setWaSessions([]); // backend down → treat as no sessions
    } finally {
      setWaLoading(false);
    }
  }, []);

  const loadLinkedIn = useCallback(async () => {
    setLiLoading(true);
    try {
      const { data } = await linkedinApi.listAccounts();
      setLiAccounts(Array.isArray(data) ? data : []);
    } catch {
      setLiAccounts([]); // 404 or backend down → treat as no accounts
    } finally {
      setLiLoading(false);
    }
  }, []);

  const loadGmail = useCallback(async () => {
    setGmLoading(true);
    try {
      const { data } = await gmailApi.status();
      setGmAccounts(Array.isArray(data?.accounts) ? data.accounts : []);
    } catch {
      setGmAccounts([]);
    } finally {
      setGmLoading(false);
    }
  }, []);

  useEffect(() => {
    loadWhatsApp();
    loadLinkedIn();
    loadGmail();
  }, [loadWhatsApp, loadLinkedIn, loadGmail]);

  return (
    <div className="mx-auto max-w-6xl">
      <h1 className="text-2xl font-bold text-zinc-50">Accounts</h1>
      <p className="mt-1 text-sm text-zinc-400">
        All your connections, in one place. Manage your main accounts and socials here.
      </p>

      <nav aria-label="Account sections" className="mt-5 flex flex-wrap gap-2">
        <Link to="/app/account#main-accounts" className="btn-secondary">Main accounts</Link>
        <Link to="/app/account#socials" className="btn-secondary">Socials</Link>
      </nav>

      <section id="main-accounts" aria-labelledby="main-accounts-title" className="mt-8 scroll-mt-20 lg:scroll-mt-6">
        <h2 id="main-accounts-title" className="text-xl font-semibold text-zinc-100">Main accounts</h2>
        <p className="mt-1 text-sm text-zinc-400">
          WhatsApp, LinkedIn and Gmail connections for your day-to-day work. Each connected account is listed
          separately — manage or add more from each one&apos;s own card.
        </p>

        {/* WhatsApp — one card per device/session */}
        <PlatformGroup
          icon={<WhatsAppIcon />}
          title="WhatsApp"
          to="/app/account/whatsapp"
          connectLabel={waLoading ? 'WhatsApp' : waSessions.length ? 'Connect another device' : 'Connect WhatsApp'}
        >
          {waLoading ? (
            <div className="card flex min-w-0 flex-col p-5">
              <div className="flex items-center gap-3 pb-5">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-surface-800 text-green-300"><WhatsAppIcon /></div>
                <p className="h-4 w-32 animate-pulse rounded bg-surface-700" />
              </div>
            </div>
          ) : waSessions.length === 0 ? (
            <EmptyConnectionCard icon={<WhatsAppIcon />} label="No WhatsApp devices connected yet." to="/app/account/whatsapp" />
          ) : (
            waSessions.map((s) => (
              <ConnectionCard
                key={s.id}
                icon={<WhatsAppIcon />}
                title={`Session #${s.id}`}
                subtitle={s.connected ? (s.is_default ? 'Default device' : 'Connected device') : (s.status === 'waiting_qr' ? 'Awaiting QR scan' : 'Not connected')}
                badge={<WhatsAppStatusBadge status={s.status} reconnectRequired={false} />}
                manageTo="/app/account/whatsapp"
                manageLabel="Manage device"
              />
            ))
          )}
        </PlatformGroup>

        {/* LinkedIn — one card per profile */}
        <PlatformGroup
          icon={<LinkedInIcon />}
          title="LinkedIn"
          to="/app/account/linkedin"
          connectLabel={liLoading ? 'LinkedIn' : liAccounts.length ? 'Connect another profile' : 'Connect LinkedIn'}
        >
          {liLoading ? (
            <div className="card flex min-w-0 flex-col p-5">
              <div className="flex items-center gap-3 pb-5">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-surface-800 text-accent-300"><LinkedInIcon /></div>
                <p className="h-4 w-32 animate-pulse rounded bg-surface-700" />
              </div>
            </div>
          ) : liAccounts.length === 0 ? (
            <EmptyConnectionCard icon={<LinkedInIcon />} label="No LinkedIn profiles connected yet." to="/app/account/linkedin" />
          ) : (
            liAccounts.map((acc) => (
              <ConnectionCard
                key={acc.id}
                icon={<LinkedInIcon />}
                title={acc.label || acc.linkedin_email}
                subtitle={acc.linkedin_email}
                badge={<AccountStatusBadge status={acc.status} />}
                manageTo="/app/account/linkedin"
                manageLabel="Manage profile"
              />
            ))
          )}
        </PlatformGroup>

        {/* Gmail — one card per mailbox */}
        <PlatformGroup
          icon={<GmailIcon />}
          title="Gmail"
          to="/app/account/gmail"
          connectLabel={gmLoading ? 'Gmail' : gmAccounts.length ? 'Connect another mailbox' : 'Connect Gmail'}
        >
          {gmLoading ? (
            <div className="card flex min-w-0 flex-col p-5">
              <div className="flex items-center gap-3 pb-5">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-surface-800 text-rose-300"><GmailIcon /></div>
                <p className="h-4 w-32 animate-pulse rounded bg-surface-700" />
              </div>
            </div>
          ) : gmAccounts.length === 0 ? (
            <EmptyConnectionCard icon={<GmailIcon />} label="No Gmail mailboxes connected yet." to="/app/account/gmail" />
          ) : (
            gmAccounts.map((acc) => (
              <ConnectionCard
                key={acc.id}
                icon={<GmailIcon />}
                title={acc.account_email}
                subtitle={acc.reconnect_required ? 'Reconnect needed' : 'Connected mailbox'}
                badge={<GmailStatusBadge status={{ connected: !acc.reconnect_required, reconnect_required: acc.reconnect_required }} />}
                manageTo="/app/account/gmail"
                manageLabel="Open mailbox"
              />
            ))
          )}
        </PlatformGroup>
      </section>

      <div className="mt-10">
        <SocialConnectionsSection />
      </div>
    </div>
  );
}
