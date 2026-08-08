import AddToLeadButton from './AddToLeadButton';
import ScoreBadge from './ScoreBadge';

/* ------------------------- small helpers ------------------------- */

// Author display name: prefer the structured first/last name fields that new
// scans store; fall back to the single full-name column for older rows.
function authorDisplayName(post) {
  const first = (post?.author_first_name || '').trim();
  const last = (post?.author_last_name || '').trim();
  if (first || last) return [first, last].filter(Boolean).join(' ');
  return (post?.author_name || '').trim() || 'LinkedIn Member';
}

// Initials from the author's name for the avatar.
function initials(post) {
  const name = authorDisplayName(post);
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] || '';
  const last = parts.length > 1 ? parts[parts.length - 1][0] : '';
  return (first + last).toUpperCase();
}

// Make old rows clickable too: older scans may have stored a relative LinkedIn
// href or only a post URN before the post_url column/migration existed.
function getLinkedInPostUrl(post) {
  const rawUrl = (post?.post_url || '').trim();
  const urn = (post?.post_urn || '').trim();

  if (rawUrl) {
    const clean = rawUrl.split('?')[0].split('#')[0];
    if (clean.startsWith('//www.linkedin.com/')) return `https:${clean}`;
    if (clean.startsWith('/')) return `https://www.linkedin.com${clean}`;
    if (clean.startsWith('www.linkedin.com/')) return `https://${clean}`;
    if (/^https?:\/\/(www\.)?linkedin\.com\/(feed\/update|posts)\//i.test(clean)) {
      return clean.replace(/^http:/i, 'https:');
    }
  }

  if (urn.startsWith('urn:li:')) {
    return `https://www.linkedin.com/feed/update/${urn}/`;
  }
  return '';
}

// Author's LinkedIn personal or organization profile URL (absolute).
function getLinkedInProfileUrl(post) {
  const raw = (post?.author_profile_url || '').trim();
  if (!raw) return '';
  if (raw.startsWith('//www.linkedin.com/')) return `https:${raw}`;
  if (raw.startsWith('/')) return `https://www.linkedin.com${raw}`;
  if (raw.startsWith('www.linkedin.com/')) return `https://${raw}`;
  if (/^https?:\/\/(www\.)?linkedin\.com\/(in|company|school|showcase)\//i.test(raw)) {
    return raw.replace(/^http:/i, 'https:');
  }
  return '';
}

// Split the poster's display name into first/last, exactly like the CSV import
// expects them.  Structured columns win; the full display name is the fallback
// for older rows ("Jane van Dijk" → first "Jane", last "van Dijk").
export function parseAuthorName(post) {
  const first = (post?.author_first_name || '').trim();
  const last = (post?.author_last_name || '').trim();
  if (first && last) return { first_name: first, last_name: last };

  const parts = authorDisplayName(post)
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .filter(Boolean);
  if (parts.length === 0) return { first_name: first, last_name: last };
  if (parts.length === 1) return { first_name: first || parts[0], last_name: last };
  return {
    first_name: first || parts[0],
    last_name: last || parts.slice(1).join(' '),
  };
}

// Only personal profiles (/in/<slug>) can become leads — company/school pages
// are not people, and the backend rejects them with the same rule.
export function personalProfileUrl(post) {
  const url = getLinkedInProfileUrl(post);
  return /^https:\/\/www\.linkedin\.com\/in\//i.test(url) ? url.replace(/\/$/, '') : '';
}

// Human-readable author/profile URL (for example, "linkedin.com/in/jane-doe") shown on the card.
function profileUrlDisplay(post) {
  const url = getLinkedInProfileUrl(post);
  if (!url) return '';
  return url.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');
}

// "1st" → "1st connection" for the metadata line above the post text.
function connectionLabel(degree) {
  if (!degree) return '';
  const d = String(degree).trim().toLowerCase();
  if (['1st', '2nd', '3rd'].includes(d)) return `${d} connection`;
  return degree;
}

// Metadata shown directly above the post text: where the post came from, the
// author's connection degree and the post time.
function postMetaLine(post) {
  const parts = ['From your feed'];
  const degree = connectionLabel(post?.connection_degree);
  if (degree) parts.push(degree);
  if (post?.post_time) parts.push(post.post_time);
  return parts.join(' · ');
}

// Deterministic avatar colour per author name.
function avatarColor(name) {
  const colors = [
    'bg-[#0a66c2]',
    'bg-emerald-600',
    'bg-violet-600',
    'bg-rose-600',
    'bg-amber-600',
    'bg-sky-600',
  ];
  let hash = 0;
  const str = name || '';
  for (let i = 0; i < str.length; i++) hash = (hash * 31 + str.charCodeAt(i)) >>> 0;
  return colors[hash % colors.length];
}

/* -------------------------- the card ---------------------------- */

/**
 * Renders a single scored post picked for the job.  The card shows the
 * author's first + last name and profile URL, the link to the real post on
 * LinkedIn, and — directly above the post text — the source metadata
 * ("From your feed · 1st connection · 5d").  The post body itself contains
 * only the actual post text (metadata is stripped at extraction time).
 */
export default function ScoredPostCard({
  post,
  rank,
  pools = [],
  currentPoolId,
  ownerEmail,
  savedState = null,
  isApplied = false,
  onMarkApplied,
  onSavedToFeedLeads,
  onDismiss,
}) {
  const postUrl = getLinkedInPostUrl(post);
  const profileUrl = getLinkedInProfileUrl(post);
  const name = authorDisplayName(post);
  const metaLine = postMetaLine(post);
  const profileDisplay = profileUrlDisplay(post);

  // Everything the Feed Leads pool needs from this card: the parsed name and
  // verified profile link the user can already see, plus the scan metadata
  // (post URL, score, matched criteria, scan id) kept for analytics.
  const leadProfile = {
    ...parseAuthorName(post),
    linkedin_url: personalProfileUrl(post),
    headline: post.author_headline || null,
  };
  const leadMetadata = {
    feed_scroll_result_id: post.id,
    source_post_url: postUrl || post.post_url || null,
    matched_score: post.score ?? null,
    matched_criteria: post.matched_terms || null,
    scan_id: post.scan_batch_id || null,
  };

  const openPost = () => {
    if (postUrl) window.open(postUrl, '_blank', 'noopener,noreferrer');
  };
  const stop = (e) => e.stopPropagation();

  // Remove this post from the results view (soft dismiss handled by the API).
  const handleDismiss = (e) => {
    stop(e);
    onDismiss?.(post);
  };

  const handleMarkApplied = (e) => {
    stop(e);
    onMarkApplied?.(post);
  };

  const avatar = (extra = '') => (
    <span
      className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white ${avatarColor(
        name
      )} ${extra}`}
    >
      {initials(post)}
    </span>
  );

  const applied = isApplied || post.is_applied || Boolean(post.applied_at);

  return (
    <div className="overflow-hidden rounded-xl border border-surface-700 bg-surface-850 shadow-lg shadow-black/20">
      {/* Top metadata strip (dark, keeps scoring info out of the "real post") */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-surface-700 text-xs font-semibold text-zinc-300">
            #{rank}
          </span>
          <span className="text-xs text-zinc-500">
            {post.scanned_at ? new Date(post.scanned_at).toLocaleString() : 'Recently scanned'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {post.score != null && <ScoreBadge score={post.score} />}
          {applied ? (
            <span className="inline-flex items-center gap-1 rounded bg-emerald-500/10 px-2 py-0.5 text-xs font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-500/20">
              Applied ✓
            </span>
          ) : onMarkApplied ? (
            <button
              type="button"
              onClick={handleMarkApplied}
              title="Mark as applied — future scans will automatically skip this post"
              className="inline-flex items-center gap-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-300 transition hover:bg-emerald-500/20 hover:text-emerald-200"
            >
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
              </svg>
              Mark as Applied
            </button>
          ) : null}
          {onDismiss && (
            <button
              type="button"
              onClick={handleDismiss}
              title="Remove this post — read it and not useful? Dismiss it from your results."
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-zinc-400 transition hover:bg-red-500/10 hover:text-red-300"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0Z" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* LinkedIn-style white post card — click anywhere to open the post */}
      <div
        onClick={postUrl ? openPost : undefined}
        role={postUrl ? 'link' : undefined}
        tabIndex={postUrl ? 0 : undefined}
        onKeyDown={(e) => {
          if (postUrl && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault();
            openPost();
          }
        }}
        className={`group block bg-white text-left transition ${
          postUrl ? 'cursor-pointer hover:bg-zinc-50' : 'cursor-default'
        }`}
      >
        {/* Header: avatar + name (both link to the profile) */}
        <div className="flex items-start justify-between gap-3 px-4 pt-4 pb-2">
          <div className="flex min-w-0 items-center gap-3">
            {profileUrl ? (
              <a
                href={profileUrl}
                target="_blank"
                rel="noopener noreferrer"
                title={profileUrl}
                onClick={stop}
                className="shrink-0 rounded-full transition hover:opacity-80"
              >
                {avatar()}
              </a>
            ) : (
              avatar()
            )}
            <div className="min-w-0">
              {profileUrl ? (
                <a
                  href={profileUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={profileUrl}
                  onClick={stop}
                  className="block truncate text-[15px] font-semibold text-zinc-900 transition hover:text-[#0a66c2] hover:underline"
                >
                  {name}
                </a>
              ) : (
                <p className="truncate text-[15px] font-semibold text-zinc-900">{name}</p>
              )}

              {/* Source metadata on top of the post text: feed source, connection, time */}
              {metaLine && <p className="truncate text-xs text-zinc-500">{metaLine}</p>}

              {/* Author profile URL */}
              {profileDisplay && (
                <a
                  href={profileUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={profileUrl}
                  onClick={stop}
                  className="mt-0.5 block truncate text-xs font-medium text-[#0a66c2] transition hover:underline"
                >
                  {profileDisplay}
                </a>
              )}
            </div>
          </div>
          {leadProfile.linkedin_url ? (
            <div className="mt-1 shrink-0">
              <AddToLeadButton
                profile={leadProfile}
                metadata={leadMetadata}
                pools={pools}
                currentPoolId={currentPoolId}
                ownerEmail={ownerEmail}
                savedState={savedState}
                onSaved={onSavedToFeedLeads}
              />
            </div>
          ) : postUrl ? (
            <span className="mt-1 inline-flex shrink-0 items-center gap-1 text-xs font-semibold text-[#0a66c2]">
              Open in LinkedIn
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
              </svg>
            </span>
          ) : null}
        </div>

        {/* Post body — real spacing & formatting, actual post text only */}
        <div className="px-4 pb-2">
          <p className="whitespace-pre-wrap break-words text-[15px] leading-[1.45] text-zinc-900">
            {post.post_text || 'This post has no text content.'}
          </p>
        </div>

        {/* Action bar */}
        <div className="mx-4 my-2 flex items-center justify-between border-t border-zinc-200 pt-1 text-zinc-600">
          <div className="flex items-center">
            <span className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition group-hover:bg-zinc-100">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6.633 10.5c.806 0 1.533-.446 2.031-1.08a9.041 9.041 0 0 1 2.861-2.4c.723-.384 1.35-.956 1.653-1.715a4.498 4.498 0 0 0 .322-1.672V3a.75.75 0 0 1 .75-.75 2.25 2.25 0 0 1 2.25 2.25c0 1.152-.26 2.243-.723 3.218-.266.558.107 1.282.725 1.282h3.126c1.026 0 1.945.694 2.054 1.715.045.422.068.85.068 1.285a11.95 11.95 0 0 1-2.649 7.521c-.388.482-.987.729-1.605.729H13.48c-.483 0-.964-.078-1.423-.23l-3.114-1.04a4.501 4.501 0 0 0-1.423-.23H5.904M14.25 9h2.25M5.904 18.75c.083.205.173.405.27.602.197.4-.078.898-.523.898h-.908c-.889 0-1.713-.518-1.972-1.368a12 12 0 0 1-.521-3.507c0-1.553.295-3.036.831-4.398C3.387 10.203 4.167 9.75 5 9.75h1.053c.472 0 .745.556.5.96a8.958 8.958 0 0 0-1.302 4.665c0 1.194.232 2.333.654 3.375Z" />
              </svg>
              Like
            </span>
            <span className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition group-hover:bg-zinc-100">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 20.25c4.97 0 9-3.694 9-8.25s-4.03-8.25-9-8.25S3 7.444 3 12c0 2.104.859 4.023 2.273 5.48.432.447.74 1.04.586 1.641a4.483 4.483 0 0 1-.923 1.785A5.969 5.969 0 0 0 6 21c1.282 0 2.47-.402 3.445-1.087.81.22 1.668.337 2.555.337Z" />
              </svg>
              Comment
            </span>
            <span className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition group-hover:bg-zinc-100">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M7.217 10.907a2.25 2.25 0 1 0 0 2.186m0-2.186c.18.324.283.696.283 1.093s-.103.77-.283 1.093m0-2.186 9.566-5.314m-9.566 7.5 9.566 5.314m0 0a2.25 2.25 0 1 0 3.935 2.186 2.25 2.25 0 0 0-3.935-2.186Zm0-12.814a2.25 2.25 0 1 0 3.933-2.185 2.25 2.25 0 0 0-3.933 2.185Z" />
              </svg>
              Repost
            </span>
            <span className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition group-hover:bg-zinc-100">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5" />
              </svg>
              Send
            </span>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            {/* Link to the post */}
            {postUrl && (
              <a
                href={postUrl}
                target="_blank"
                rel="noopener noreferrer"
                title={`Post link: ${postUrl}`}
                onClick={stop}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-semibold text-[#0a66c2] transition hover:bg-[#0a66c2]/10"
              >
                <span className="sr-only">Post link</span>
                Open post
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                </svg>
              </a>
            )}
          </div>
        </div>
      </div>

      {/* Matched terms — kept below the white card */}
      {post.matched_terms && post.matched_terms.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 px-4 py-3">
          <span className="text-xs font-medium text-zinc-400">Matched:</span>
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
      )}
    </div>
  );
}
