export default function ScoreBadge({ score }) {
  const getColor = () => {
    if (score >= 80) return 'text-green-400 bg-green-500/10 ring-green-500/20';
    if (score >= 60) return 'text-yellow-400 bg-yellow-500/10 ring-yellow-500/20';
    if (score >= 40) return 'text-orange-400 bg-orange-500/10 ring-orange-500/20';
    return 'text-zinc-400 bg-zinc-500/10 ring-zinc-500/20';
  };

  const getIcon = () => {
    if (score >= 80) return '🔥';
    if (score >= 60) return '✨';
    return '';
  };

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-semibold ring-1 ring-inset ${getColor()}`}
    >
      {getIcon()} {score.toFixed(1)}/100
    </span>
  );
}
