import { Link, NavLink } from 'react-router-dom';
import { INBOX_CHANNELS } from '../../constants/inbox';
import { PlatformIcon } from '../social/SocialBits';

export function ChannelIcon({ channel, className = 'h-10 w-10 rounded-xl bg-accent-500/10 text-accent-300' }) {
  if (channel === 'instagram') return <PlatformIcon platform="instagram" className={className} />;
  return (
    <span className={`inline-flex shrink-0 items-center justify-center ${className}`}>
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 11.5a9 9 0 0 1-9 9 10 10 0 0 1-4-.8L3 21l1.3-4.5A9 9 0 1 1 21 11.5Z" />
        {channel === 'messenger' ? (
          <path strokeLinecap="round" strokeLinejoin="round" d="m6.5 14 4-4 3 2 4-3.5-4 5-3-2-4 2.5Z" />
        ) : channel === 'whatsapp-business' ? (
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 16V7h3a2.2 2.2 0 0 1 0 4.4H9h3.5a2.3 2.3 0 0 1 0 4.6H9Z" />
        ) : (
          <path strokeLinecap="round" strokeLinejoin="round" d="M8 7.5c0 4.7 3.3 8 8 8l1-2.5-3-1-1 1c-1.4-.6-2.4-1.6-3-3l1-1-1-3-2 .5Z" />
        )}
      </svg>
    </span>
  );
}

export function InboxPageHeader({ channel, description, action }) {
  const current = INBOX_CHANNELS.find((item) => item.id === channel);
  return (
    <div className="shrink-0 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-accent-400">Ultimate Inbox</p>
          <div className="mt-2 flex items-center gap-3">
            <ChannelIcon channel={channel} />
            <h1 className="text-2xl font-bold text-zinc-100">{current.label}</h1>
          </div>
          {description && <p className="mt-2 max-w-2xl text-sm text-zinc-400">{description}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link to={current.accountPath} className="btn-secondary">Manage connection</Link>
          {action}
        </div>
      </div>
      <nav aria-label="Ultimate inbox channels" className="flex gap-1 overflow-x-auto border-b border-surface-700 scrollbar-thin">
        {INBOX_CHANNELS.map((item) => (
          <NavLink
            key={item.id}
            to={item.to}
            className={({ isActive }) => `shrink-0 border-b-2 px-3 py-2 text-sm font-medium transition ${isActive ? 'border-accent-400 text-accent-300' : 'border-transparent text-zinc-400 hover:text-zinc-200'}`}
          >
            {item.label}
            {item.comingSoon && <span className="ml-2 text-[10px] uppercase text-zinc-500">Soon</span>}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
