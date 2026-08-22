import Modal from './Modal';

/**
 * Login popup shown whenever the app discovers the session is no longer
 * valid — either after a page refresh fails the boot-time session check, or
 * when a background API call hits an unrecoverable 401. By the time it
 * appears the user has already been routed to the login page; the dialog
 * simply explains why, and its action confirms the login form.
 */
export default function SessionExpiredDialog({ open, onClose }) {
  return (
    <Modal open={open} onClose={onClose} title="Session expired">
      <div className="space-y-5" data-testid="session-expired-dialog">
        <p className="text-sm leading-relaxed text-zinc-400">
          Your session has expired or is no longer valid. Please log in again to pick up
          right where you left off.
        </p>
        <button type="button" className="btn-primary w-full" onClick={onClose}>
          Log in
        </button>
      </div>
    </Modal>
  );
}
