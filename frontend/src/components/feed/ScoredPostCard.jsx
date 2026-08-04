import ScoreBadge from './ScoreBadge';

/**
 * Renders a single scored post as a Google-style search preview on the job
 * details page:
 *   • the post URL is shown prominently (green link + breadcrumb), exactly
 *     like a Google result, and links straight to the LinkedIn post;
 *   • below it sits the full post text;
 *   • the score badge, author, timestamp and matched terms stay visible.
 */
export default function ScoredPostCard({ post, rank }) {
  const postUrl = post.post_url;

  // Show the raw URL as a breadcrumb (e.g. www.linkedin.com › feed › update › …)
  const displayUrl = () => {
    if (!postUrl) return '';
    const pretty = postUrl
      .replace(/^https?:\/\//, '')
      .replace(/\/+$/, '')
      .replace(/\/(feed|update)\//gi, ' › ');
    return pretty;
  };

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-800 p-5 transition hover:border-surface-600">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="flex items-center gap-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-700 text-sm font-semibold text-zinc-300">
            #{rank}
          </span>
          <div>
            {post.author_name && (
              <p className="font-medium text-zinc-100">{post.author_name}</p>
            )}
            <p className="text-xs text-zinc-500">
              {new Date(post.scanned_at).toLocaleString()}
            </p>
          </div>
        </div>
        <ScoreBadge score={post.score} />
      </div>

      {/* Google-style preview: clickable link + breadcrumb */}
      <div className="mb-3">
        {postUrl ? (
          <>
            <a
              href={postUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="block truncate text-sm font-medium text-accent-400 transition hover:text-accent-300"
            >
              View post on LinkedIn ↗
            </a>
            <a
              href={postUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-0.5 block truncate text-xs text-green-400/90 hover:text-green-300"
              title={postUrl}
            >
              {displayUrl()}
            </a>
          </>
        ) : (
          <p className="text-xs text-zinc-500">No post link available</p>
        )}
      </div>

      {/* Post full text */}
      <div className="mb-3 rounded-lg bg-surface-900 p-3">
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-300">
          {post.post_text}
        </p>
      </div>

      {post.matched_terms && post.matched_terms.length > 0 && (
        <div className="mb-3">
          <p className="mb-1.5 text-xs font-medium text-zinc-400">Matched terms:</p>
          <div className="flex flex-wrap gap-1.5">
            {post.matched_terms.map((term, idx) => (
              <span
                key={idx}
                className="inline-flex items-center gap-1 rounded-md bg-green-500/10 px-2 py-0.5 text-xs text-green-300 ring-1 ring-inset ring-green-500/20"
              >
                <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
                {term}
              </span>
            ))}
          </div>
        </div>
      )}

      {postUrl && (
        <a
          href={postUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-accent-400 transition hover:text-accent-300"
        >
          Open post
          <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
          </svg>
        </a>
      )}
    </div>
  );
}
