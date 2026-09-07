import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { linkedinApi, whatsappApi } from '../api/endpoints';
import { gmailApi } from '../api/gmail';
import { socialSchedulerApi, PLATFORMS } from '../api/socialScheduler';
import { PlatformIcon } from '../components/social/SocialBits';
import { ChannelIcon } from '../components/inbox/InboxBits';
import {
  GmailGlyph,
  LinkedInGlyph,
  PlatformSummaryCard,
  WhatsAppGlyph,
} from '../components/accounts/AccountCards';

/**
 * Accounts hub — a summary only.
 *
 * Every platform gets one card that says whether it is connected and how many
 * accounts are connected, plus a single button to its manage page. No account
 * names, no connect buttons and no per-account actions live here: those all
 * belong to the manage pages (e.g. /app/account/linkedin).
 */
export default function AccountsPage() {
  const { hash, key, pathname } = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  useEffect(() => {
    if (hash === '#socials' || hash === '#main-accounts') {
      document.getElementById(hash.slice(1))?.scrollIntoView({ block: 'start' });
    }
  }, [hash, key]);

  // WhatsApp — connected devices/sessions.
  const [waLoading, setWaLoading] = useState(true);
  const [waSessions, setWaSessions] = useState([]);
  // LinkedIn — connected profiles.
  const [liLoading, setLiLoading] = useState(true);
  const [liAccounts, setLiAccounts] = useState([]);
  // Gmail — connected mailboxes.
  const [gmLoading, setGmLoading] = useState(true);
  const [gmStatus, setGmStatus] = useState(null);
  // Socials — YouTube / Facebook / Instagram / TikTok.
  const [socialLoading, setSocialLoading] = useState(true);
  const [socialError, setSocialError] = useState(null);
  const [connections, setConnections] = useState([]);

  const loadWhatsApp = useCallback(async () => {
    setWaLoading(true);
    try {
      const { data } = await whatsappApi.listSessions();
      const list = Array.isArray(data?.sessions) ? data.sessions : [];
      if (list.length) {
        setWaSessions(list);
        return;
      }
      // Older backends (and installs with a single device) only expose status.
      const { data: status } = await whatsappApi.getStatus();
      setWaSessions(status?.status === 'connected' ? [{ id: 'default', status: status.status, connected: true }] : []);
    } catch {
      try {
        const { data: status } = await whatsappApi.getStatus();
        setWaSessions(status?.status === 'connected' ? [{ id: 'default', status: status.status, connected: true }] : []);
      } catch {
        setWaSessions([]); // backend down → treat as no devices
      }
    } finally {
      setWaLoading(false);
    }
  }, []);

  const loadLinkedIn = useCallback(async () => {
    setLiLoading(true);
    try {
      const { data } = await linkedinApi.listAccounts();
      const list = Array.isArray(data) ? data : [];
      if (list.length) {
        setLiAccounts(list);
        return;
      }
      const { data: single } = await linkedinApi.getAccount();
      setLiAccounts(single ? [single] : []);
    } catch {
      try {
        const { data: single } = await linkedinApi.getAccount();
        setLiAccounts(single ? [single] : []);
      } catch {
        setLiAccounts([]); // 404 or backend down → treat as no profiles
      }
    } finally {
      setLiLoading(false);
    }
  }, []);

  const loadGmail = useCallback(async () => {
    setGmLoading(true);
    try {
      const { data } = await gmailApi.status();
      setGmStatus(data || null);
    } catch {
      setGmStatus(null);
    } finally {
      setGmLoading(false);
    }
  }, []);

  const loadSocials = useCallback(async () => {
    setSocialLoading(true);
    setSocialError(null);
    try {
      const { data } = await socialSchedulerApi.listPlatforms();
      setConnections(Array.isArray(data) ? data : []);
    } catch {
      setSocialError('Could not load your social connections.');
      setConnections([]);
    } finally {
      setSocialLoading(false);
    }
  }, []);

  useEffect(() => {
    loadWhatsApp();
    loadLinkedIn();
    loadGmail();
    loadSocials();
  }, [loadWhatsApp, loadLinkedIn, loadGmail, loadSocials]);

  // A platform OAuth round-trip returns the browser here (the backend's
  // configured return URL). Report the outcome and hand the user over to that
  // platform's manage page, where the account now shows up.
  useEffect(() => {
    const platform = searchParams.get('platform');
    if (!platform) return;
    const label = PLATFORMS.find((p) => p.id === platform)?.label || platform;
    const error = searchParams.get('error');
    const connected = searchParams.get('connected') === '1';
    if (connected) toast.success(`${label} connected`);
    else if (error) toast.error(`${label}: ${error}`, { duration: 8000 });

    const remaining = new URLSearchParams(searchParams);
    ['platform', 'connected', 'error'].forEach((param) => remaining.delete(param));
    const known = PLATFORMS.some((p) => p.id === platform);
    navigate(
      known
        ? { pathname: `/app/account/social/${platform}`, search: remaining.toString() }
        : { pathname, search: remaining.toString(), hash: '#socials' },
      { replace: true },
    );
  }, [searchParams, pathname, navigate]);

  const gmailAccounts = useMemo(() => {
    if (Array.isArray(gmStatus?.accounts) && gmStatus.accounts.length) return gmStatus.accounts;
    if (gmStatus?.connected) return [{ id: 'default', account_email: gmStatus.account_email }];
    return [];
  }, [gmStatus]);

  const waConnected = waSessions.filter((s) => s.connected || s.status === 'connected');
  const liActive = liAccounts.filter((a) => a.status === 'active');
  const gmAttention = gmailAccounts.some((m) => m.reconnect_required);

  const mainCards = [
    {
      key: 'whatsapp',
      title: 'WhatsApp',
      icon: <WhatsAppGlyph />,
      iconClass: 'bg-green-500/10 text-green-300',
      loading: waLoading,
      count: waSessions.length,
      noun: { one: 'device', many: 'devices' },
      state: waSessions.length === 0 ? 'none' : waConnected.length ? 'connected' : 'pending',
      manageTo: '/app/account/whatsapp',
      manageLabel: 'Manage devices',
    },
    {
      key: 'linkedin',
      title: 'LinkedIn',
      icon: <LinkedInGlyph />,
      iconClass: 'bg-accent-500/10 text-accent-300',
      loading: liLoading,
      count: liAccounts.length,
      noun: { one: 'profile', many: 'profiles' },
      state: liAccounts.length === 0 ? 'none' : liActive.length ? 'connected' : 'pending',
      manageTo: '/app/account/linkedin',
      manageLabel: 'Manage profiles',
    },
    {
      key: 'gmail',
      title: 'Gmail',
      icon: <GmailGlyph />,
      iconClass: 'bg-rose-500/10 text-rose-300',
      loading: gmLoading,
      count: gmailAccounts.length,
      noun: { one: 'mailbox', many: 'mailboxes' },
      state: gmailAccounts.length === 0 ? 'none' : gmAttention ? 'attention' : 'connected',
      manageTo: '/app/account/gmail',
      manageLabel: 'Manage mailboxes',
    },
  ];

  return (
    <div className="mx-auto max-w-6xl">
      <h1 className="text-2xl font-bold text-zinc-50">Accounts</h1>
      <p className="mt-1 text-sm text-zinc-400">
        An overview of your connections. Open a platform to see its accounts, add another one or disconnect.
      </p>

      <section id="main-accounts" aria-labelledby="main-accounts-title" className="mt-8 scroll-mt-20 lg:scroll-mt-6">
        <h2 id="main-accounts-title" className="text-xl font-semibold text-zinc-100">Main accounts</h2>
        <p className="mt-1 text-sm text-zinc-400">WhatsApp, LinkedIn and Gmail — your day-to-day connections.</p>

        <div className="mt-5 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {mainCards.map((card) => (
            <PlatformSummaryCard
              key={card.key}
              testId={`account-card-${card.key}`}
              icon={card.icon}
              iconClass={card.iconClass}
              title={card.title}
              loading={card.loading}
              state={card.state}
              count={card.count}
              noun={card.noun}
              manageTo={card.manageTo}
              manageLabel={card.manageLabel}
            />
          ))}
        </div>
      </section>

      <section
        id="socials"
        aria-labelledby="socials-title"
        className="mt-10 scroll-mt-20 border-t border-surface-700 pt-8 lg:scroll-mt-6"
      >
        <h2 id="socials-title" className="text-xl font-semibold text-zinc-100">Socials</h2>
        <p className="mt-1 text-sm text-zinc-400">
          The social accounts used by Social Scheduler and Ultimate Inbox. Tokens are stored encrypted.
        </p>

        {socialError && (
          <div className="mt-4 rounded-xl border border-red-500/20 bg-red-500/5 p-4" role="alert">
            <p className="text-sm text-red-300">{socialError}</p>
            <button type="button" className="btn-secondary mt-3" onClick={loadSocials}>
              Retry connections
            </button>
          </div>
        )}

        <div className="mt-5 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {PLATFORMS.map((platform) => {
            const conn = connections.find((c) => c.platform === platform.id);
            const accounts = Array.isArray(conn?.accounts)
              ? conn.accounts
              : conn?.connected
                ? [{ id: 'default' }]
                : [];
            const attention = Boolean(conn?.reconnect_required) || accounts.some((a) => a.reconnect_required);
            const state = accounts.length === 0 ? 'none' : attention ? 'attention' : 'connected';
            return (
              <PlatformSummaryCard
                key={platform.id}
                testId={`platform-card-${platform.id}`}
                icon={<PlatformIcon platform={platform.id} className="h-6 w-6" />}
                iconClass={accounts.length ? 'bg-accent-500/15 text-accent-300' : 'bg-surface-700 text-zinc-400'}
                title={platform.label}
                hint={!socialLoading && conn && !conn.configured ? 'Not set up on this instance yet.' : undefined}
                loading={socialLoading}
                state={state}
                count={accounts.length}
                manageTo={`/app/account/social/${platform.id}`}
                manageLabel="Manage accounts"
              />
            );
          })}

          <PlatformSummaryCard
            testId="platform-card-whatsapp-business"
            icon={<ChannelIcon channel="whatsapp-business" className="h-6 w-6" />}
            iconClass="bg-green-500/10 text-green-300"
            title="WhatsApp Business"
            hint="Business connections and messaging arrive in a future update."
            state="soon"
            count={0}
            manageLabel="Coming soon"
            disabled
          />
        </div>
      </section>
    </div>
  );
}
