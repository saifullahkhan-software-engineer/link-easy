import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import ScoreBadge from './ScoreBadge';

/* ------------------------- small helpers ------------------------- */

// Relative time label like LinkedIn ("just now", "2h", "3d").
function timeAgo(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const diffMs = Date.now() - d.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `${weeks}w`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo`;
  return `${Math.floor(days / 365)}y`;
}

// Initials from the author's name for the avatar.
function initials(name) {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] || '';
  const last = parts.length > 1 ? parts[parts.length - 1][0] : '';
  return (first + last).toUpperCase();
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
 * Renders a single scored post the way it actually looks on LinkedIn — a white
 * card with the author header, the full post text formatted with correct
 * spacing, and an action bar. A 3-dot (⋯) menu in the header exposes "Copy
 * link" so the user can grab the URL to the real post.
 */
export default function ScoredPostCard({ post, rank }) {
  const postUrl = post.post_url;

  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  // Close the 3-dot menu when clicking anywhere outside of it.
  useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [menuOpen]);

  const copyLink = async () => {
    if (!postUrl) {
      toast.error('No post link available to copy');
      setMenuOpen(false);
      return;
    }
    try {
      await navigator.clipboard.writeText(postUrl);
      toast.success('Post link copied to clipboard');
    } catch {
      // Fallback for non-secure contexts / older browsers.
      try {
        const ta = document.createElement('textarea');
        ta.value = postUrl;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        toast.success('Post link copied to clipboard');
      } catch {
        toast.error('Could not copy the link');
      }
    } finally {
      setMenuOpen(false);
    }
  };

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
        {post.score != null && <ScoreBadge score={post.score} />}
      </div>

      {/* LinkedIn-style white post card */}
      <div className="bg-white">
        {/* Header: avatar, name, time + 3-dot menu */}
        <div className="relative flex items-start justify-between gap-3 px-4 pt-4 pb-3">
          <div className="flex min-w-0 items-center gap-3">
            <span
              className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white ${avatarColor(
                post.author_name
              )}`}
            >
              {initials(post.author_name)}
            </span>
            <div className="min-w-0">
              <p className="truncate text-[15px] font-semibold text-zinc-900">
                {post.author_name || 'LinkedIn Member'}
              </p>
              <p className="text-xs text-zinc-500">
                {timeAgo(post.scanned_at) ? `${timeAgo(post.scanned_at)} · ` : ''}
                {postUrl ? 'Post' : 'Feed post'}
              </p>
            </div>
          </div>

          {/* 3-dot menu */}
          <div className="relative shrink-0" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((o) => !o)}
              aria-label="Post options"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              className="flex h-9 w-9 items-center justify-center rounded-full text-zinc-500 transition hover:bg-zinc-100 hover:text-zinc-700"
            >
              <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                <circle cx="5" cy="12" r="1.8" />
                <circle cx="12" cy="12" r="1.8" />
                <circle cx="19" cy="12" r="1.8" />
              </svg>
            </button>

            {menuOpen && (
              <div
                role="menu"
                className="absolute right-0 top-11 z-20 w-52 overflow-hidden rounded-lg border border-zinc-200 bg-white py-1.5 shadow-xl"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={copyLink}
                  disabled={!postUrl}
                  className="flex w-full items-center gap-2.5 px-3.5 py-2 text-left text-sm text-zinc-700 transition hover:bg-zinc-100 disabled:cursor-not-allowed disabled:text-zinc-400"
                >
                  <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244" />
                  </svg>
                  Copy link
                </button>
                {postUrl && (
                  <a
                    role="menuitem"
                    href={postUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex w-full items-center gap-2.5 px-3.5 py-2 text-left text-sm text-zinc-700 transition hover:bg-zinc-100"
                  >
                    <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                    </svg>
                    Open in LinkedIn
                  </a>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Post body — real spacing & formatting */}
        <div className="px-4 pb-2">
          <p className="whitespace-pre-wrap break-words text-[15px] leading-[1.45] text-zinc-900">
            {post.post_text || 'This post has no text content.'}
          </p>
        </div>

        {/* Action bar */}
        <div className="mx-4 my-2 flex items-center justify-between border-t border-zinc-200 pt-1 text-zinc-600">
          <div className="flex items-center">
            <span className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition hover:bg-zinc-100">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6.633 10.5c.806 0 1.533-.446 2.031-1.08a9.041 9.041 0 0 1 2.861-2.4c.723-.384 1.35-.956 1.653-1.715a4.498 4.498 0 0 0 .322-1.672V3a.75.75 0 0 1 .75-.75 2.25 2.25 0 0 1 2.25 2.25c0 1.152-.26 2.243-.723 3.218-.266.558.107 1.282.725 1.282h3.126c1.026 0 1.945.694 2.054 1.715.045.422.068.85.068 1.285a11.95 11.95 0 0 1-2.649 7.521c-.388.482-.987.729-1.605.729H13.48c-.483 0-.964-.078-1.423-.23l-3.114-1.04a4.501 4.501 0 0 0-1.423-.23H5.904M14.25 9h2.25M5.904 18.75c.083.205.173.405.27.602.197.4-.078.898-.523.898h-.908c-.889 0-1.713-.518-1.972-1.368a12 12 0 0 1-.521-3.507c0-1.553.295-3.036.831-4.398C3.387 10.203 4.167 9.75 5 9.75h1.053c.472 0 .745.556.5.96a8.958 8.958 0 0 0-1.302 4.665c0 1.194.232 2.333.654 3.375Z" />
              </svg>
              Like
            </span>
            <span className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition hover:bg-zinc-100">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 20.25c4.97 0 9-3.694 9-8.25s-4.03-8.25-9-8.25S3 7.444 3 12c0 2.104.859 4.023 2.273 5.48.432.447.74 1.04.586 1.641a4.483 4.483 0 0 1-.923 1.785A5.969 5.969 0 0 0 6 21c1.282 0 2.47-.402 3.445-1.087.81.22 1.668.337 2.555.337Z" />
              </svg>
              Comment
            </span>
            <span className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition hover:bg-zinc-100">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M7.217 10.907a2.25 2.25 0 1 0 0 2.186m0-2.186c.18.324.283.696.283 1.093s-.103.77-.283 1.093m0-2.186 9.566-5.314m-9.566 7.5 9.566 5.314m0 0a2.25 2.25 0 1 0 3.935 2.186 2.25 2.25 0 0 0-3.935-2.186Zm0-12.814a2.25 2.25 0 1 0 3.933-2.185 2.25 2.25 0 0 0-3.933 2.185Z" />
              </svg>
              Repost
            </span>
            <span className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition hover:bg-zinc-100">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5" />
              </svg>
              Send
            </span>
          </div>
          {postUrl && (
            <a
              href={postUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium text-[#0a66c2] transition hover:bg-blue-50"
            >
              Open post
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
              </svg>
            </a>
          )}
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
