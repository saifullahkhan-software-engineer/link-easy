export function Metric({ label, value, detail, tone = 'zinc' }) {
  const tones = {
    zinc: 'border-surface-700 bg-surface-900 text-zinc-100',
    emerald: 'border-emerald-500/20 bg-emerald-500/5 text-emerald-200',
    amber: 'border-amber-500/20 bg-amber-500/5 text-amber-200',
    indigo: 'border-indigo-500/20 bg-indigo-500/5 text-indigo-200',
  };
  return (
    <div className={`rounded-xl border p-4 ${tones[tone] || tones.zinc}`}>
      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-zinc-500">{label}</p>
      <p className="mt-3 text-3xl font-bold tracking-tight">{value}</p>
      {detail ? <p className="mt-1 text-xs text-zinc-500">{detail}</p> : null}
    </div>
  );
}

export function StatusPills({ map }) {
  const entries = Object.entries(map || {});
  if (!entries.length) return <p className="text-sm text-zinc-500">None yet.</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="inline-flex items-center gap-2 rounded-lg border border-surface-700 bg-surface-800 px-3 py-1.5 text-xs text-zinc-300"
        >
          <span className="font-medium text-zinc-400">{key}</span>
          <span className="font-bold text-zinc-100">{value}</span>
        </span>
      ))}
    </div>
  );
}

export function Section({ title, description, children, actions }) {
  return (
    <section className="rounded-2xl border border-surface-700 bg-surface-900/60 p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-zinc-100">{title}</h2>
          {description ? <p className="mt-1 text-sm text-zinc-500">{description}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}
