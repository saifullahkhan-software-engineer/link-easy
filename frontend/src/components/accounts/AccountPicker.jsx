import { Spinner } from '../Spinner';

/**
 * Reusable account picker used by the multi-account surfaces (socials, Gmail,
 * LinkedIn, WhatsApp, Meta inbox).
 *
 * `accounts` is the list returned by the relevant endpoint; each entry is
 * rendered as an <option>. `getKey` returns the value sent back to the API
 * (a connection id / account id / session id / mailbox id); `getLabel` and
 * `getSublabel` produce the visible text. `value` / `onChange` wire it to the
 * page's selection state (typically via useStoredAccountId).
 *
 * When there is exactly one account the select is shown but disabled, since
 * there is nothing to choose between — the only option is implicit.
 */
export default function AccountPicker({
  accounts = [],
  value,
  onChange,
  getKey = (a) => a.id,
  getLabel = (a) => a.account_name || a.account_email || a.label || a.id,
  getSublabel = () => null,
  disabled = false,
  placeholder = 'Select an account',
  loading = false,
  error = null,
  id,
  className = '',
  hideLabel = false,
}) {
  const list = Array.isArray(accounts) ? accounts : [];
  const hasAccounts = list.length > 0;

  if (loading) {
    return (
      <div className={className}>
        {!hideLabel && <span className="input-label">Account</span>}
        <div className="flex items-center gap-2 text-sm text-zinc-400">
          <Spinner className="h-4 w-4" /> Loading accounts…
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={className}>
        {!hideLabel && <span className="input-label">Account</span>}
        <p className="text-xs text-amber-300">{error}</p>
      </div>
    );
  }

  if (!hasAccounts) {
    return (
      <div className={className}>
        {!hideLabel && <span className="input-label">Account</span>}
        <p className="text-xs text-zinc-500">No accounts connected.</p>
      </div>
    );
  }

  return (
    <div className={className}>
      {!hideLabel && (
        <label htmlFor={id} className="input-label">
          Account
        </label>
      )}
      <select
        id={id}
        value={value ?? ''}
        disabled={disabled || list.length === 1}
        onChange={(e) => onChange(e.target.value)}
        className="input-field"
        data-testid={id ? `account-picker-${id}` : 'account-picker'}
      >
        {list.length > 1 && <option value="">{placeholder}</option>}
        {list.map((a) => {
          const key = String(getKey(a));
          const sub = getSublabel(a);
          return (
            <option key={key} value={key}>
              {getLabel(a)}
              {sub ? ` — ${sub}` : ''}
            </option>
          );
        })}
      </select>
    </div>
  );
}
