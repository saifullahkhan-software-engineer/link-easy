/**
 * Shared status badge for the WhatsApp connection.
 * Used on the Accounts hub, the WhatsApp connect page and the scanner page.
 *
 * `reconnectRequired` (from GET /whatsapp/status) is true when the database
 * says "connected" but the durable Chromium profile was wiped (e.g. a deploy
 * without the /app/profiles volume). In that case the green "Connected"
 * would be a lie — the next browser launch lands on a blank QR screen — so
 * the badge switches to an amber "Profile missing" state instead.
 */
const STATUS_MAP = {
  disconnected: {
    bg: 'bg-red-500/10',
    text: 'text-red-300',
    ring: 'ring-red-500/20',
    label: 'Disconnected',
  },
  waiting_qr: {
    bg: 'bg-yellow-500/10',
    text: 'text-yellow-300',
    ring: 'ring-yellow-500/20',
    label: 'Waiting for QR scan',
  },
  connected: {
    bg: 'bg-green-500/10',
    text: 'text-green-300',
    ring: 'ring-green-500/20',
    label: 'Connected',
  },
  reconnect_required: {
    bg: 'bg-amber-500/10',
    text: 'text-amber-300',
    ring: 'ring-amber-500/20',
    label: 'Profile missing — rescan QR',
  },
  error: {
    bg: 'bg-red-500/10',
    text: 'text-red-300',
    ring: 'ring-red-500/20',
    label: 'Error',
  },
};

export default function WhatsAppStatusBadge({ status, reconnectRequired = false }) {
  const effective = status === 'connected' && reconnectRequired ? 'reconnect_required' : status;
  const s = STATUS_MAP[effective] || STATUS_MAP.disconnected;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${s.bg} ${s.text} ${s.ring}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          effective === 'connected'
            ? 'bg-green-400 animate-pulse'
            : effective === 'reconnect_required'
              ? 'bg-amber-400 animate-pulse'
              : 'bg-current'
        }`}
      />
      {s.label}
    </span>
  );
}
