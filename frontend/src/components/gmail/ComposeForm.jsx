import { useState } from 'react';
import toast from 'react-hot-toast';
import { gmailApi } from '../../api/gmail';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../Spinner';

const MAX_BODY = 100_000;

/**
 * Compose form used both by the inbox's reply modal and the standalone
 * /app/gmail/compose page.
 *
 * Only To is required; From is always the connected mailbox (the backend
 * refuses anything else — Gmail only sends as the authenticated user).
 */
export default function ComposeForm({ initial = {}, onSent, onCancel, submitLabel = 'Send' }) {
  const [fields, setFields] = useState({
    to: initial.to || '',
    cc: initial.cc || '',
    bcc: initial.bcc || '',
    subject: initial.subject || '',
    body: initial.body || '',
  });
  const [sending, setSending] = useState(false);

  function set(field) {
    return (event) => setFields((f) => ({ ...f, [field]: event.target.value }));
  }

  async function handleSend(event) {
    event.preventDefault();
    if (!fields.to.trim()) {
      toast.error('Add at least one recipient');
      return;
    }
    if (!fields.subject.trim() && !fields.body.trim()) {
      toast.error('Write a subject or a message first');
      return;
    }
    setSending(true);
    try {
      const { data } = await gmailApi.send({
        to: fields.to.trim(),
        cc: fields.cc.trim(),
        bcc: fields.bcc.trim(),
        subject: fields.subject.trim(),
        body: fields.body,
      });
      toast.success(`Message sent to ${data.to || 'recipient'}`);
      onSent?.(data);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not send the message'));
    } finally {
      setSending(false);
    }
  }

  return (
    <form onSubmit={handleSend} className="space-y-3">
      <div>
        <label className="input-label" htmlFor="gmail-to">To</label>
        <input
          id="gmail-to"
          className="input-field"
          placeholder="name@example.com, another@example.com"
          value={fields.to}
          onChange={set('to')}
          autoFocus
          required
        />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="input-label" htmlFor="gmail-cc">Cc</label>
          <input id="gmail-cc" className="input-field" placeholder="Optional" value={fields.cc} onChange={set('cc')} />
        </div>
        <div>
          <label className="input-label" htmlFor="gmail-bcc">Bcc</label>
          <input id="gmail-bcc" className="input-field" placeholder="Optional" value={fields.bcc} onChange={set('bcc')} />
        </div>
      </div>
      <div>
        <label className="input-label" htmlFor="gmail-subject">Subject</label>
        <input id="gmail-subject" className="input-field" value={fields.subject} onChange={set('subject')} maxLength={300} />
      </div>
      <div>
        <label className="input-label" htmlFor="gmail-body">Message</label>
        <textarea
          id="gmail-body"
          className="input-field min-h-[9rem] resize-y font-normal"
          value={fields.body}
          onChange={set('body')}
          maxLength={MAX_BODY}
          placeholder="Write your message…"
        />
        <p className="mt-1 text-right text-[11px] tabular-nums text-zinc-600">
          {fields.body.length.toLocaleString()} / {MAX_BODY.toLocaleString()}
        </p>
      </div>

      <div className="flex items-center justify-end gap-3 border-t border-surface-700 pt-4">
        {onCancel && (
          <button type="button" className="btn-secondary" onClick={onCancel} disabled={sending}>
            Cancel
          </button>
        )}
        <button type="submit" className="btn-primary" disabled={sending}>
          {sending ? <Spinner className="h-4 w-4" /> : null}
          {sending ? 'Sending…' : submitLabel}
        </button>
      </div>
    </form>
  );
}
