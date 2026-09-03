import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { socialSchedulerApi } from '../../api/socialScheduler';
import { getErrorMessage } from '../../api/client';
import Modal from '../../components/Modal';
import { Spinner } from '../../components/Spinner';
import {
  PlatformChip,
  PostStatusBadge,
  SocialPageHeader,
  formatDateTime,
  formatTime,
} from '../../components/social/SocialBits';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const STATUS_DOT = {
  pending: 'bg-amber-400',
  posting: 'bg-indigo-400',
  posted: 'bg-emerald-400',
  failed: 'bg-red-400',
  cancelled: 'bg-zinc-500',
};

const pad = (n) => String(n).padStart(2, '0');
const monthKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/**
 * Month grid of scheduled and published posts. Days are computed in the
 * browser's local timezone from each post's scheduled_at, so a post at
 * 01:00 local shows on the local day even if it is the previous day in UTC.
 */
export default function SocialCalendarPage() {
  const [cursor, setCursor] = useState(() => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1);
  });
  const [posts, setPosts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedDay, setSelectedDay] = useState(null);

  const load = useCallback(async (monthStart) => {
    setLoading(true);
    try {
      // Fetch by scheduled_at range (local month, padded a day each side for
      // timezone offsets) rather than the UTC-bucketed /calendar endpoint so
      // the grid matches what the user sees in their own timezone.
      const from = new Date(monthStart.getFullYear(), monthStart.getMonth(), 1 - 1);
      const to = new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 1 + 1);
      const { data } = await socialSchedulerApi.listPosts({
        from: from.toISOString(),
        to: to.toISOString(),
        limit: 500,
      });
      setPosts(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load the calendar'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(cursor);
  }, [cursor, load]);

  const byDay = useMemo(() => {
    const map = {};
    for (const post of posts) {
      const key = dayKey(new Date(post.scheduled_at));
      (map[key] = map[key] || []).push(post);
    }
    for (const list of Object.values(map)) {
      list.sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at));
    }
    return map;
  }, [posts]);

  // Build the grid: Monday-first, leading/trailing blanks.
  const cells = useMemo(() => {
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    const first = new Date(year, month, 1);
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const lead = (first.getDay() + 6) % 7; // Sunday=0 → 6
    const list = [];
    for (let i = 0; i < lead; i += 1) list.push(null);
    for (let d = 1; d <= daysInMonth; d += 1) list.push(new Date(year, month, d));
    while (list.length % 7 !== 0) list.push(null);
    return list;
  }, [cursor]);

  const today = dayKey(new Date());
  const monthLabel = cursor.toLocaleString(undefined, { month: 'long', year: 'numeric' });
  const monthTotal = posts.filter((p) => monthKey(new Date(p.scheduled_at)) === monthKey(cursor)).length;

  return (
    <div className="mx-auto max-w-6xl">
      <SocialPageHeader
        current="/app/social-scheduler/calendar"
        title="Calendar"
        description="A month at a glance. Click a day to see its posts."
        action={
          <Link
            to="/app/social-scheduler/schedule"
            className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-accent-500 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-accent-400"
          >
            Schedule Post
          </Link>
        }
      />

      <div className="card overflow-hidden">
        <div className="flex items-center justify-between border-b border-surface-700 px-5 py-3">
          <button
            className="btn-secondary !px-2.5 !py-1.5"
            onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
            aria-label="Previous month"
          >
            ‹
          </button>
          <div className="text-center">
            <h2 className="text-base font-semibold text-zinc-100">{monthLabel}</h2>
            <p className="text-xs text-zinc-500">
              {loading ? 'Loading…' : `${monthTotal} post${monthTotal === 1 ? '' : 's'} this month`}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="btn-secondary !px-3 !py-1.5 text-xs"
              onClick={() => {
                const d = new Date();
                setCursor(new Date(d.getFullYear(), d.getMonth(), 1));
              }}
            >
              Today
            </button>
            <button
              className="btn-secondary !px-2.5 !py-1.5"
              onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
              aria-label="Next month"
            >
              ›
            </button>
          </div>
        </div>

        <div className="grid grid-cols-7 border-b border-surface-700 bg-surface-800/60 text-center text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
          {WEEKDAYS.map((d) => (
            <div key={d} className="py-2">
              {d}
            </div>
          ))}
        </div>

        {loading ? (
          <div className="flex h-64 items-center justify-center">
            <Spinner />
          </div>
        ) : (
          <div className="grid grid-cols-7">
            {cells.map((date, i) => {
              if (!date) return <div key={`blank-${i}`} className="min-h-[104px] border-b border-r border-surface-700/60 bg-surface-900/40" />;
              const key = dayKey(date);
              const dayPosts = byDay[key] || [];
              const isToday = key === today;
              const isPast = date < new Date(new Date().setHours(0, 0, 0, 0));
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => dayPosts.length && setSelectedDay({ key, date, posts: dayPosts })}
                  className={`flex min-h-[104px] flex-col items-stretch border-b border-r border-surface-700/60 p-2 text-left transition ${
                    dayPosts.length ? 'hover:bg-surface-800' : 'cursor-default'
                  } ${isPast ? 'bg-surface-900/30' : ''}`}
                  data-testid="calendar-day"
                  data-date={key}
                >
                  <span
                    className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium ${
                      isToday ? 'bg-accent-500 text-white' : isPast ? 'text-zinc-600' : 'text-zinc-300'
                    }`}
                  >
                    {date.getDate()}
                  </span>
                  <div className="mt-1 space-y-1">
                    {dayPosts.slice(0, 3).map((post) => (
                      <div
                        key={post.id}
                        className="flex items-center gap-1.5 truncate rounded bg-surface-800 px-1.5 py-0.5 text-[11px] text-zinc-300"
                        title={`${formatTime(post.scheduled_at)} · ${post.title}`}
                      >
                        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOT[post.status] || 'bg-zinc-500'}`} />
                        <span className="shrink-0 tabular-nums text-zinc-500">{formatTime(post.scheduled_at)}</span>
                        <span className="truncate">{post.title}</span>
                      </div>
                    ))}
                    {dayPosts.length > 3 && (
                      <p className="px-1 text-[11px] text-zinc-500">+{dayPosts.length - 3} more</p>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-4 px-5 py-3 text-xs text-zinc-500">
          {Object.entries(STATUS_DOT).map(([status, cls]) => (
            <span key={status} className="inline-flex items-center gap-1.5 capitalize">
              <span className={`h-1.5 w-1.5 rounded-full ${cls}`} />
              {status === 'pending' ? 'scheduled' : status === 'posted' ? 'published' : status}
            </span>
          ))}
        </div>
      </div>

      <Modal
        open={Boolean(selectedDay)}
        onClose={() => setSelectedDay(null)}
        title={selectedDay ? selectedDay.date.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }) : ''}
        wide
      >
        <div className="space-y-3">
          {selectedDay?.posts.map((post) => (
            <div key={post.id} className="rounded-lg border border-surface-700 bg-surface-800 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium text-zinc-100">{post.title}</p>
                <PostStatusBadge status={post.status} />
              </div>
              <p className="mt-1 text-xs text-zinc-500">{formatDateTime(post.scheduled_at)}</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(post.platforms || []).map((p) => (
                  <PlatformChip key={p} platform={p} />
                ))}
              </div>
            </div>
          ))}
          <div className="flex justify-end pt-2">
            <Link
              to={selectedDay?.posts.some((p) => p.status === 'posted' || p.status === 'failed') ? '/app/social-scheduler/history' : '/app/social-scheduler/queue'}
              className="btn-secondary"
              onClick={() => setSelectedDay(null)}
            >
              Open in list →
            </Link>
          </div>
        </div>
      </Modal>
    </div>
  );
}
