export const PASSWORD_RULES = [
  { id: 'length', label: 'At least 8 characters', test: (p) => p.length >= 8 },
  { id: 'lower', label: 'One lowercase letter', test: (p) => /[a-z]/.test(p) },
  { id: 'upper', label: 'One uppercase letter', test: (p) => /[A-Z]/.test(p) },
  { id: 'number', label: 'One number', test: (p) => /\d/.test(p) },
  { id: 'special', label: 'One special character', test: (p) => /[^A-Za-z0-9]/.test(p) },
];

export const passwordIsValid = (password) => PASSWORD_RULES.every((r) => r.test(password));

/** Live rule checklist shown under password inputs — mirrors backend rules exactly. */
export default function PasswordStrength({ password }) {
  return (
    <ul className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
      {PASSWORD_RULES.map((rule) => {
        const ok = password.length > 0 && rule.test(password);
        return (
          <li
            key={rule.id}
            className={`flex items-center gap-1.5 text-xs transition-colors ${
              ok ? 'text-emerald-400' : 'text-zinc-500'
            }`}
          >
            {ok ? (
              <svg className="h-3.5 w-3.5 shrink-0" viewBox="0 0 20 20" fill="currentColor">
                <path
                  fillRule="evenodd"
                  d="M16.7 5.3a1 1 0 0 1 0 1.4l-8 8a1 1 0 0 1-1.4 0l-4-4a1 1 0 1 1 1.4-1.4L8 12.58l7.3-7.3a1 1 0 0 1 1.4 0Z"
                  clipRule="evenodd"
                />
              </svg>
            ) : (
              <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border border-zinc-600 text-[9px] text-zinc-600">
                ·
              </span>
            )}
            {rule.label}
          </li>
        );
      })}
    </ul>
  );
}
