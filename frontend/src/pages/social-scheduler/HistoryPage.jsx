import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { socialSchedulerApi, PLATFORMS } from '../../api/socialScheduler';
import { getErrorMessage } from '../../api/client';
import Modal from '../../components/Modal';
import { Spinner } from '../../components/Spinner';
import {
  EmptyState,
  GroupShareChecklist,
  PlatformChip,
  PlatformIcon,
  PostStatusBadge,
  SocialPageHeader,
  formatDateTime,
  shortError,
} from '../../components/social/SocialBits';

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'posted', label: 'Published' },
  { id: 'failed', label: 'Failed' },
];

/**
 * History: finished posts with the per-platform outcome (link to the live
 * video, or the failure reason), plus lifetime totals per platform.
 */
export default function SocialHistoryPage() {
  const [posts, setPosts] = useState([]);
  const [stats, setStats] = useState(null);
  const [filter, setFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [postsRes, statsRes] = await Promise.all([
        socialSchedulerApi.listPosts({ status: 'posted,failed', limit: 300 }),
        socialSchedulerApi.getStats(),
      ]);
      setPosts(Array.isArray(postsRes.data) ? postsRes.data : []);
      setStats(statsRes.data);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load history'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const visible = posts
    .filter((p) => filter === 'all' || p.status === filter)
    .sort((a, b) => new Date(b.updated_at || b.scheduled_at) - new Date(a.updated_at || a.scheduled_at));

  const handleDelete = async () => {
    if (!deleting) return;
    setBusy(true);
    try {
      await socialSchedulerApi.deletePost(deleting.id);
      toast.success('Removed from history');
      setPosts((cur) => cur.filter((p) => p.id !== deleting.id));
      setDeleting(null);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to delete'));
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl">
      <SocialPageHeader
        current="/app/social-scheduler/history"
        title="History"
        description="Everything that has been published — or tried to be — with links to the live videos."
      />

      {/* Per-platform totals */}
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        {PLATFORMS.map((p) => {
          const counts = stats?.per_platform?.[p.id] || { posted: 0, failed: 0 };
          return (
            <div key={p.id} className="card flex items-center gap-4 p-4">
              <PlatformIcon platform={p.id} className="h-10 w-10 rounded-lg bg-surface-700 text-zinc-300" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-zinc-200">{p.label}</p>
                <p className="text-xs text-zinc-500">
                  <span className="text-emerald-300">{counts.posted} published</span>
                  {' · '}
                  <span className={counts.failed ? 'text-red-300' : ''}>{counts.failed} failed</span>
                </p>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mb-4 flex items-center gap-1 rounded-lg border border-surface-700 bg-surface-800 p-1 w-fit">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            onClick={() => setFilter(f.id)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
              filter === f.id ? 'bg-surface-700 text-zinc-100' : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <EmptyState
          icon="📊"
          title={filter === 'all' ? 'Nothing published yet' : `No ${filter === 'posted' ? 'published' : 'failed'} posts`}
          description="Once a scheduled post goes out it shows up here with links to each platform."
          action={posts.length === 0 ? { to: '/app/social-scheduler/schedule', label: 'Schedule a post' } : undefined}
        />
      ) : (
        <div className="space-y-3">
          {visible.map((post) => (
            <div key={post.id} className="card p-5" data-testid="history-post" data-status={post.status}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate text-base font-semibold text-zinc-100">{post.title}</h3>
                    <PostStatusBadge status={post.status} />
                  </div>
                  <p className="mt-1 text-sm text-zinc-400">
                    Scheduled for {formatDateTime(post.scheduled_at)}
                  </p>
                </div>
                <button
                  className="btn-secondary !px-3 !py-1.5 text-xs"
                  onClick={() => setDeleting(post)}
                  aria-label={`Delete ${post.title}`}
                >
                  Delete
                </button>
              </div>

              <ul className="mt-4 divide-y divide-surface-700 rounded-lg border border-surface-700">
                {(post.platforms || []).map((platform) => {
                  const r = (post.results || []).find((x) => x.platform === platform);
                  return (
                    <li key={platform} className="flex flex-wrap items-center gap-3 px-3 py-2.5 text-sm">
                      <PlatformChip platform={platform} withLabel={false} />
                      <span className="w-32 shrink-0 text-zinc-300">
                        {PLATFORMS.find((p) => p.id === platform)?.label || platform}
                      </span>
                      {r?.status === 'posted' ? (
                        <>
                          <span className="text-emerald-300">Published</span>
                          {r.platform_url && (
                            <a
                              href={r.platform_url}
                              target="_blank"
                              rel="noreferrer"
                              className="truncate text-accent-400 underline-offset-2 hover:underline"
                            >
                              {r.platform_url}
                            </a>
                          )}
                          {r.posted_at && (
                            <span className="ml-auto text-xs text-zinc-500">{formatDateTime(r.posted_at)}</span>
                          )}
                        </>
                      ) : r?.status === 'failed' ? (
                        <span className="min-w-0 flex-1 text-red-200" title={r.error}>
                          Failed — {shortError(r.error, 160) || 'no details'}
                        </span>
                      ) : (
                        <span className="text-zinc-500">{r?.status || 'no result recorded'}</span>
                      )}
                      {/* Non-fatal detail on a successful publish — e.g. which
                          playlists YouTube could not update. Deliberately not
                          in `error`, which only renders for failures. */}
                      {r?.note && (
                        <span className="w-full text-xs text-amber-300" title={r.note} data-testid="result-note">
                          {r.note}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
              {post.facebook_groups?.length > 0 && (
                <GroupShareChecklist
                  groups={post.facebook_groups}
                  caption={[post.caption, post.hashtags].filter(Boolean).join('\n\n')}
                />
              )}
            </div>
          ))}
        </div>
      )}

      <Modal open={Boolean(deleting)} onClose={() => setDeleting(null)} title="Delete from history?">
        <p className="text-sm text-zinc-300">
          This removes <span className="font-medium text-zinc-100">{deleting?.title}</span> and its uploaded video
          from LinkEasy only — anything already published stays live on the platform.
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <button className="btn-secondary" onClick={() => setDeleting(null)} disabled={busy}>
            Keep
          </button>
          <button className="btn-danger" onClick={handleDelete} disabled={busy}>
            {busy && <Spinner />}
            Delete
          </button>
        </div>
      </Modal>
    </div>
  );
}
