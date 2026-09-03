import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { socialSchedulerApi, PLATFORMS } from '../../api/socialScheduler';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../../components/Spinner';
import SchedulingDisabledNotice from '../../components/SchedulingDisabledNotice';
import {
  EmptyState,
  PlatformChip,
  PostStatusBadge,
  SocialPageHeader,
  formatDateTime,
  formatRelative,
} from '../../components/social/SocialBits';

/**
 * Social scheduler overview: headline numbers, the next few scheduled posts
 * and the most recent outcomes. Everything here is scoped to the signed-in
 * user by the backend.
 */
export default function SocialSchedulerDashboard() {
  const [posts, setPosts] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (silent = false) => {
    try {
      const [postsRes, statsRes] = await Promise.all([
        socialSchedulerApi.listPosts({ limit: 100 }),
        socialSchedulerApi.getStats(),
      ]);
      setPosts(Array.isArray(postsRes.data) ? postsRes.data : []);
      setStats(statsRes.data);
    } catch (err) {
      if (!silent) toast.error(getErrorMessage(err, 'Failed to load the social scheduler'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(() => load(true), 30_000);
    return () => clearInterval(interval);
  }, [load]);

  const upcoming = posts
    .filter((p) => p.status === 'pending' || p.status === 'posting')
    .sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at))
    .slice(0, 5);
  const recent = posts
    .filter((p) => p.status === 'posted' || p.status === 'failed')
    .sort((a, b) => new Date(b.updated_at || b.scheduled_at) - new Date(a.updated_at || a.scheduled_at))
    .slice(0, 5);

  const connected = stats?.connected_platforms || [];
  const missing = PLATFORMS.filter((p) => !connected.includes(p.id));

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl">
      <SocialPageHeader
        current="/app/social-scheduler"
        title="Overview"
        description={
          stats?.next_post_at
            ? `Next post goes live ${formatRelative(stats.next_post_at)} — ${formatDateTime(stats.next_post_at)}.`
            : 'Schedule one video to YouTube Shorts, Instagram Reels and TikTok at once.'
        }
        action={
          <Link
            to="/app/social-scheduler/schedule"
            className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-accent-500 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-accent-400"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
            Schedule Post
          </Link>
        }
      />

      <SchedulingDisabledNotice className="mb-6" />

      {/* Stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Scheduled (next 7 days)" value={stats?.scheduled_this_week ?? 0} tone="text-accent-300" />
        <StatCard label="In queue" value={stats?.total_scheduled ?? 0} tone="text-amber-300" />
        <StatCard label="Published" value={stats?.total_published ?? 0} tone="text-emerald-300" />
        <StatCard
          label="Failed"
          value={stats?.total_failed ?? 0}
          tone={stats?.total_failed ? 'text-red-300' : 'text-zinc-300'}
        />
      </div>

      {/* Connection nudge */}
      {missing.length > 0 && (
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-surface-700 bg-surface-800 px-5 py-4">
          <div>
            <p className="text-sm font-medium text-zinc-200">
              {connected.length === 0 ? 'No platforms connected yet' : 'Some platforms are not connected'}
            </p>
            <p className="mt-0.5 text-xs text-zinc-400">
              Posts to {missing.map((p) => p.label).join(', ')} will fail until you connect{' '}
              {missing.length === 1 ? 'it' : 'them'}.
            </p>
          </div>
          <Link to="/app/social-scheduler/settings" className="btn-secondary">
            Connect platforms →
          </Link>
        </div>
      )}

      <div className="mt-8 grid gap-6 lg:grid-cols-2">
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold text-zinc-100">Upcoming</h2>
            <Link to="/app/social-scheduler/queue" className="text-sm text-accent-400 hover:text-accent-300">
              View queue →
            </Link>
          </div>
          {upcoming.length === 0 ? (
            <EmptyState
              icon="🗓️"
              title="No upcoming posts"
              description="Schedule your first video to get started."
              action={{ to: '/app/social-scheduler/schedule', label: 'Schedule a post' }}
            />
          ) : (
            <div className="space-y-3">
              {upcoming.map((post) => (
                <MiniPostCard key={post.id} post={post} />
              ))}
            </div>
          )}
        </section>

        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold text-zinc-100">Recent activity</h2>
            <Link to="/app/social-scheduler/history" className="text-sm text-accent-400 hover:text-accent-300">
              View history →
            </Link>
          </div>
          {recent.length === 0 ? (
            <EmptyState icon="📊" title="No activity yet" description="Published and failed posts will appear here." />
          ) : (
            <div className="space-y-3">
              {recent.map((post) => (
                <MiniPostCard key={post.id} post={post} />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function StatCard({ label, value, tone }) {
  return (
    <div className="card p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</p>
      <p className={`mt-2 text-3xl font-bold tabular-nums ${tone}`}>{value}</p>
    </div>
  );
}

function MiniPostCard({ post }) {
  return (
    <Link
      to={`/app/social-scheduler/queue?post=${post.id}`}
      className="card block p-4 transition hover:border-surface-600"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-zinc-100">{post.title}</p>
          <p className="mt-0.5 text-xs text-zinc-500">
            {formatDateTime(post.scheduled_at)} · {formatRelative(post.scheduled_at)}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {(post.platforms || []).map((p) => (
              <PlatformChip key={p} platform={p} />
            ))}
          </div>
        </div>
        <PostStatusBadge status={post.status} />
      </div>
    </Link>
  );
}
