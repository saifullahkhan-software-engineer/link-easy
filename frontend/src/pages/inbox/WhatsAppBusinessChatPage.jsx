import { Link } from 'react-router-dom';
import { ChannelIcon, InboxPageHeader } from '../../components/inbox/InboxBits';

export default function WhatsAppBusinessChatPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <InboxPageHeader channel="whatsapp-business" description="A dedicated home for your business conversations." />
      <section className="card mx-auto max-w-2xl border-dashed px-6 py-12 text-center" aria-labelledby="business-chat-title">
        <ChannelIcon channel="whatsapp-business" className="mx-auto h-16 w-16 rounded-2xl bg-green-500/10 text-green-300" />
        <p className="mt-5 text-xs font-semibold uppercase tracking-wider text-amber-300">Coming soon</p>
        <h2 id="business-chat-title" className="mt-2 text-xl font-semibold text-zinc-100">WhatsApp Business Chat</h2>
        <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-zinc-400">
          WhatsApp Business connections and messaging are coming in a future update. This channel is not available yet.
          Your existing WhatsApp connection and chat are unchanged.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <Link to="/app/inbox/whatsapp" className="btn-primary">Open WhatsApp Chat</Link>
          <Link to="/app/account#socials" className="btn-secondary">View social accounts</Link>
        </div>
      </section>
    </div>
  );
}
