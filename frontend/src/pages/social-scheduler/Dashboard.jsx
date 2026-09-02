import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { socialSchedulerApi } from '../../api/socialScheduler';
import { formatDistanceToNow } from 'date-fns';

const platformColors = {
  youtube: '#ff0000',
  instagram: '#e1306c',
  tiktok: '#fe2c55',
};

const platformIcons = {
  youtube: '▶',
  instagram: '📷',
  tiktok: '♪',
};

function StatusBadge({ status }) {
  const config = {
    pending: { bg: 'bg-[#161b22] border border-[#f59e0b]', text: 'text-[#f59e0b]', dot: 'bg-[#f59e0b]', label: 'Scheduled' },
    posting: { bg: 'bg-[#161b22] border border-[#6366f1]', text: 'text-[#6366f1]', dot: 'bg-[#6366f1]', label: 'Posting...' },
    posted: { bg: 'bg-[#161b22] border border-[#10b981]', text: 'text-[#10b981]', dot: 'bg-[#10b981]', label: 'Published' },
    failed: { bg: 'bg-[#161b22] border border-[#ef4444]', text: 'text-[#ef4444]', dot: 'bg-[#ef4444]', label: 'Failed' },
    cancelled: { bg: 'bg-[#161b22] border border-[#6e7681]', text: 'text-[#6e7681]', dot: 'bg-[#6e7681]', label: 'Cancelled' },
  }[status] || { bg: 'bg-[#21262d]', text: 'text-[#8b949e]', dot: 'bg-[#8b949e]', label: status };

  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${config.bg} ${config.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${config.dot}`} />
      {config.label}
    </span>
  );
}

export default function SocialSchedulerDashboard() {
  const [posts, setPosts] = useState([]);
  const [stats, setStats] = useState({
    scheduled_this_week: 0,
    total_published: 0,
    total_failed: 0,
    next_post_in: null,
    connected_platforms: [],
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);

  async function fetchData() {
    try {
      const [postsRes, statsRes] = await Promise.all([
        socialSchedulerApi.getPosts(),
        socialSchedulerApi.getStats(),
      ]);
      setPosts(postsRes);
      setStats(statsRes);
    } catch (error) {
      console.error('Failed to fetch data:', error);
    } finally {
      setLoading(false);
    }
  }

  const upcomingPosts = posts
    .filter((p) => p.status === 'pending')
    .sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at))
    .slice(0, 5);

  const recentPosts = posts
    .filter((p) => p.status !== 'pending')
    .sort((a, b) => new Date(b.scheduled_at) - new Date(a.scheduled_at))
    .slice(0, 5);

  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';

  return (
    <div className="p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">{greeting} 👋</h1>
          <p className="text-[#8b949e] mt-1">
            {stats.next_post_in
              ? `Next post goes live ${stats.next_post_in}`
              : 'No posts scheduled yet. Ready to post?'}
          </p>
        </div>
        <Link
          to="/app/social-scheduler/schedule"
          className="px-5 py-2.5 bg-[#7c3aed] hover:bg-[#6d28d9] text-white rounded-lg font-medium text-sm transition-colors flex items-center gap-2"
        >
          <span className="text-lg">+</span> Schedule Post
        </Link>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        <StatCard
          label="Scheduled (7 days)"
          value={String(stats.scheduled_this_week)}
          icon="📅"
          color="text-[#6366f1]"
          loading={loading}
        />
        <StatCard
          label="Published"
          value={String(stats.total_published)}
          icon="✅"
          color="text-[#10b981]"
          loading={loading}
        />
        <StatCard
          label="Failed"
          value={String(stats.total_failed)}
          icon="❌"
          color="text-[#ef4444]"
          loading={loading}
        />
        <StatCard
          label="Platforms Connected"
          value={`${stats.connected_platforms.length}/3`}
          icon="🔗"
          color="text-[#f59e0b]"
          loading={loading}
        />
      </div>

      {/* Platform connection banner */}
      {stats.connected_platforms.length < 3 && !loading && (
        <div className="mb-6 p-4 bg-[#161b22] border border-[#30363d] rounded-xl flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🔌</span>
            <div>
              <p className="text-white font-medium text-sm">Connect your platforms</p>
              <p className="text-[#8b949e] text-xs">
                {3 - stats.connected_platforms.length} platform
                {3 - stats.connected_platforms.length > 1 ? 's' : ''} not connected yet
              </p>
            </div>
          </div>
          <Link
            to="/app/social-scheduler/settings"
            className="px-4 py-2 bg-[#21262d] hover:bg-[#30363d] text-[#e6edf3] rounded-lg text-sm font-medium transition-colors"
          >
            Connect Now →
          </Link>
        </div>
      )}

      {/* Two column layout */}
      <div className="grid grid-cols-2 gap-6">
        {/* Upcoming Posts */}
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-white font-semibold">Upcoming Posts</h2>
            <Link to="/app/social-scheduler/queue" className="text-[#7c3aed] text-sm hover:text-[#a78bfa]">
              View all →
            </Link>
          </div>
          <div className="space-y-3">
            {loading ? (
              Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="h-20 bg-[#21262d] rounded-xl animate-pulse" />
              ))
            ) : upcomingPosts.length === 0 ? (
              <EmptyState
                icon="📭"
                title="No upcoming posts"
                description="Schedule your first post to get started"
                action={{ href: '/app/social-scheduler/schedule', label: 'Schedule Now' }}
              />
            ) : (
              upcomingPosts.map((post) => <MiniPostCard key={post.id} post={post} />)
            )}
          </div>
        </div>

        {/* Recent Activity */}
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-white font-semibold">Recent Activity</h2>
            <Link to="/app/social-scheduler/history" className="text-[#7c3aed] text-sm hover:text-[#a78bfa]">
              View all →
            </Link>
          </div>
          <div className="space-y-3">
            {loading ? (
              Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="h-20 bg-[#21262d] rounded-xl animate-pulse" />
              ))
            ) : recentPosts.length === 0 ? (
              <EmptyState
                icon="📊"
                title="No activity yet"
                description="Published posts will appear here"
              />
            ) : (
              recentPosts.map((post) => <MiniPostCard key={post.id} post={post} />)
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, icon, color, loading }) {
  return (
    <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xl">{icon}</span>
        <p className="text-[#6e7681] text-xs font-medium">{label}</p>
      </div>
      {loading ? (
        <div className="h-8 w-16 bg-[#21262d] rounded animate-pulse" />
      ) : (
        <p className={`text-3xl font-bold ${color}`}>{value}</p>
      )}
    </div>
  );
}

function MiniPostCard({ post }) {
  const platforms = Array.isArray(post.platforms) ? post.platforms : JSON.parse(post.platforms || '[]');
  return (
    <div className="bg-[#161b22] border border-[#30363d] hover:border-[#484f58] rounded-xl p-4 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-white text-sm font-medium truncate">{post.title}</p>
          <p className="text-[#6e7681] text-xs mt-1">
            {post.status === 'pending'
              ? `🕒 ${new Date(post.scheduled_at).toLocaleString()}`
              : `📤 ${new Date(post.scheduled_at).toLocaleString()}`}
          </p>
          <div className="flex gap-1 mt-2">
            {platforms.map((p) => (
              <span
                key={p}
                className="text-xs px-1.5 py-0.5 rounded text-white font-medium"
                style={{ backgroundColor: platformColors[p] || '#6e7681' }}
              >
                {p.slice(0, 2).toUpperCase()}
              </span>
            ))}
          </div>
        </div>
        <StatusBadge status={post.status} />
      </div>
    </div>
  );
}

function EmptyState({ icon, title, description, action }) {
  return (
    <div className="bg-[#161b22] border border-dashed border-[#30363d] rounded-xl p-6 text-center">
      <div className="text-3xl mb-2">{icon}</div>
      <p className="text-[#8b949e] font-medium text-sm">{title}</p>
      <p className="text-[#484f58] text-xs mt-1">{description}</p>
      {action && (
        <Link
          to={action.href}
          className="inline-block mt-3 px-4 py-1.5 bg-[#7c3aed] hover:bg-[#6d28d9] text-white rounded-lg text-xs font-medium transition-colors"
        >
          {action.label}
        </Link>
      )}
    </div>
  );
}
