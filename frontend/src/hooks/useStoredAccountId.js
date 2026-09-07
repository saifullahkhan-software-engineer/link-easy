import { useEffect, useState } from 'react';

/**
 * Persist a selected account/session id in localStorage so the multi-account
 * pickers keep the user's last choice across page reloads.
 *
 * The stored value is the opaque id the backend uses to address a specific
 * connection (a social `connection_id`, Gmail `account_id`, LinkedIn `account_id`,
 * WhatsApp `session_id`, or a Meta inbox `account.id`). An empty string means
 * "use the server's default (first-connected) account" — which is exactly the
 * legacy behaviour, so older installs are unchanged.
 */
export function useStoredAccountId(storageKey) {
  const [value, setValue] = useState(() => {
    try {
      return localStorage.getItem(storageKey) || '';
    } catch {
      return '';
    }
  });

  useEffect(() => {
    try {
      if (value) localStorage.setItem(storageKey, value);
      else localStorage.removeItem(storageKey);
    } catch {
      // Storage can throw in private mode or when over quota — non-fatal.
    }
  }, [storageKey, value]);

  return [value, setValue];
}
