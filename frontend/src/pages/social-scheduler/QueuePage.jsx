import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { socialSchedulerApi, PLATFORMS } from '../../api/socialScheduler';
import { getErrorMessage } from '../../api/client';
import Modal from '../../components/Modal';
import { Spinner } from '../../components/Spinner';
import SchedulingDisabledNotice from '../../components/SchedulingDisabledNotice';
import {
  EmptyState,
  PlatformChip,
  PostStatusBadge,
  SocialPageHeader,
  formatDateTime,
  formatRelative,
  fromLocalInputValue,
  shortError,
  toLocalInputValue,
} from '../../components/social/SocialBits';

/**
 * Queue: every post that has not finished yet (scheduled, publishing,
 * failed, cancelled). Published posts live on the History page.
 */
export default function SocialQueuePage() {
  const [posts, setPosts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [busy, setBusy] = useState(false);
  const [searchParams] = useSearchParams();
  const highlight = searchParams.get('post');

  const load = useCallback(async (silent = false) => {
    try {
      const { data } = await socialSchedulerApi.listPosts({ status: 'pending,posting,failed,cancelled', limit: 200 });
      setPosts(Array.isArray(data) ? data : []);
    } catch (err) {
      if (!silent) toast.error(getErrorMessage(err, 'Failed to load the queue'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(() => load(true), 20_000);
    return () => clearInterval(interval);
  }, [load]);

  const act = async (label, fn) => {
    setBusy(true);
    try {
      await fn();
      toast.success(label);
      await load(true);
      return true;
    } catch (err) {
      toast.error(getErrorMessage(err, `Failed: ${label.toLowerCase()}`));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const groups = {
    active: posts
      .filter((p) => p.status === 'pending' || p.status === 'posting')
      .sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at)),
    attention: posts
      .filter((p) => p.status === 'failed' || p.status === 'cancelled')
      .sort((a, b) => new Date(b.updated_at || b.scheduled_at) - new Date(a.updated_at || a.scheduled_at)),
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
        current="/app/social-scheduler/queue"
        title="Queue"
        description="Posts waiting to go out, plus anything that failed or was cancelled and can be re-queued."
      />

      <SchedulingDisabledNotice className="mb-6" />

      {posts.length === 0 ? (
        <EmptyState
          icon="🗓️"
          title="The queue is empty"
          description="Nothing is waiting to be published."
          action={{ to: '/app/social-scheduler/schedule', label: 'Schedule a post' }}
        />
      ) : (
        <div className="space-y-8">
          <Section title={`Scheduled (${groups.active.length})`}>
            {groups.active.length === 0 ? (
              <p className="text-sm text-zinc-500">No posts scheduled.</p>
            ) : (
              groups.active.map((post) => (
                <PostRow
                  key={post.id}
                  post={post}
                  highlighted={post.id === highlight}
                  busy={busy}
                  onEdit={() => setEditing(post)}
                  onCancel={() => act('Post cancelled', () => socialSchedulerApi.cancelPost(post.id))}
                  onDelete={() => setDeleting(post)}
                />
              ))
            )}
          </Section>

          {groups.attention.length > 0 && (
            <Section title={`Needs attention (${groups.attention.length})`}>
              {groups.attention.map((post) => (
                <PostRow
                  key={post.id}
                  post={post}
                  highlighted={post.id === highlight}
                  busy={busy}
                  onEdit={() => setEditing(post)}
                  onRequeue={() =>
                    act('Post re-queued', () =>
                      socialSchedulerApi.requeuePost(
                        post.id,
                        // A past time would be rejected — push it 5 min out.
                        new Date(post.scheduled_at).getTime() < Date.now()
                          ? new Date(Date.now() + 5 * 60_000).toISOString()
                          : undefined
                      )
                    )
                  }
                  onDelete={() => setDeleting(post)}
                />
              ))}
            </Section>
          )}
        </div>
      )}

      <EditPostModal
        post={editing}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await load(true);
        }}
      />

      <Modal open={Boolean(deleting)} onClose={() => setDeleting(null)} title="Delete post?">
        <p className="text-sm text-zinc-300">
          <span className="font-medium text-zinc-100">{deleting?.title}</span> and its uploaded video will be
          removed. This cannot be undone.
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <button className="btn-secondary" onClick={() => setDeleting(null)} disabled={busy}>
            Keep
          </button>
          <button
            className="btn-danger"
            disabled={busy}
            onClick={async () => {
              const ok = await act('Post deleted', () => socialSchedulerApi.deletePost(deleting.id));
              if (ok) setDeleting(null);
            }}
          >
            {busy && <Spinner />}
            Delete
          </button>
        </div>
      </Modal>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-400">{title}</h2>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function PostRow({ post, highlighted, busy, onEdit, onCancel, onRequeue, onDelete }) {
  const failures = (post.results || []).filter((r) => r.status === 'failed');
  const platformResult = (id) => (post.results || []).find((r) => r.platform === id);
  return (
    <div
      className={`card p-5 ${highlighted ? 'ring-1 ring-accent-500/50' : ''}`}
      data-testid="queue-post"
      data-status={post.status}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-base font-semibold text-zinc-100">{post.title}</h3>
            <PostStatusBadge status={post.status} />
          </div>
          <p className="mt-1 text-sm text-zinc-400">
            {formatDateTime(post.scheduled_at)}
            {post.status === 'pending' && (
              <span className="text-zinc-500"> · {formatRelative(post.scheduled_at)}</span>
            )}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {(post.platforms || []).map((p) => {
              const r = platformResult(p);
              const tone =
                r?.status === 'posted' ? 'opacity-100' : r?.status === 'failed' ? 'ring-red-500/60' : '';
              return <PlatformChip key={p} platform={p} className={tone} />;
            })}
          </div>
          {post.caption && <p className="mt-2 line-clamp-2 text-sm text-zinc-500">{post.caption}</p>}
          {failures.length > 0 && (
            <ul className="mt-3 space-y-1 rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2">
              {failures.map((r) => (
                <li key={r.id} className="text-xs text-red-200">
                  <span className="font-semibold">{PLATFORMS.find((p) => p.id === r.platform)?.label || r.platform}:</span>{' '}
                  {shortError(r.error) || 'Failed'}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex shrink-0 flex-wrap gap-2">
          {post.status === 'pending' && (
            <>
              <button className="btn-secondary !px-3 !py-1.5 text-xs" onClick={onEdit} disabled={busy}>
                Edit
              </button>
              <button className="btn-secondary !px-3 !py-1.5 text-xs" onClick={onCancel} disabled={busy}>
                Cancel
              </button>
            </>
          )}
          {(post.status === 'failed' || post.status === 'cancelled') && (
            <>
              <button className="btn-secondary !px-3 !py-1.5 text-xs" onClick={onEdit} disabled={busy}>
                Edit
              </button>
              <button className="btn-primary !px-3 !py-1.5 text-xs" onClick={onRequeue} disabled={busy}>
                Re-queue
              </button>
            </>
          )}
          {post.status !== 'posting' && (
            <button className="btn-danger !px-3 !py-1.5 text-xs" onClick={onDelete} disabled={busy}>
              Delete
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function EditPostModal({ post, onClose, onSaved }) {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!post) return setForm(null);
    setForm({
      title: post.title || '',
      caption: post.caption || '',
      hashtags: post.hashtags || '',
      platforms: post.platforms || [],
      scheduled_at: toLocalInputValue(post.scheduled_at),
      youtube_title: post.youtube_title || '',
      instagram_caption: post.instagram_caption || '',
      tiktok_caption: post.tiktok_caption || '',
    });
  }, [post]);

  if (!post || !form) return null;

  const update = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }));
  const toggle = (id) =>
    setForm((f) => ({
      ...f,
      platforms: f.platforms.includes(id) ? f.platforms.filter((p) => p !== id) : [...f.platforms, id],
    }));

  const save = async (e) => {
    e.preventDefault();
    if (!form.platforms.length) return toast.error('Pick at least one platform');
    const when = fromLocalInputValue(form.scheduled_at);
    const requeue = post.status !== 'pending';
    if (new Date(when).getTime() < Date.now() - 60_000) return toast.error('Pick a time in the future');
    setSaving(true);
    try {
      await socialSchedulerApi.updatePost(post.id, {
        title: form.title.trim(),
        caption: form.caption,
        hashtags: form.hashtags,
        platforms: form.platforms,
        scheduled_at: when,
        youtube_title: form.youtube_title,
        instagram_caption: form.instagram_caption,
        tiktok_caption: form.tiktok_caption,
        ...(requeue ? { status: 'pending' } : {}),
      });
      toast.success(requeue ? 'Post updated and re-queued' : 'Post updated');
      onSaved();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to update the post'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open onClose={onClose} title="Edit post" wide>
      <form onSubmit={save} className="space-y-4">
        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">Title</label>
          <input className="input-field" value={form.title} onChange={update('title')} maxLength={200} required />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">Caption</label>
          <textarea className="input-field min-h-[80px]" value={form.caption} onChange={update('caption')} maxLength={5000} />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">Hashtags</label>
            <input className="input-field" value={form.hashtags} onChange={update('hashtags')} maxLength={1000} />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">Publish at</label>
            <input type="datetime-local" className="input-field" value={form.scheduled_at} onChange={update('scheduled_at')} required />
          </div>
        </div>
        <div>
          <p className="mb-1.5 block text-sm font-medium text-zinc-300">Platforms</p>
          <div className="flex flex-wrap gap-2">
            {PLATFORMS.map((p) => {
              const on = form.platforms.includes(p.id);
              return (
                <button
                  type="button"
                  key={p.id}
                  onClick={() => toggle(p.id)}
                  aria-pressed={on}
                  className={`rounded-lg border px-3 py-1.5 text-sm transition ${
                    on
                      ? 'border-accent-500/60 bg-accent-500/10 text-accent-200'
                      : 'border-surface-600 bg-surface-800 text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  {p.label}
                </button>
              );
            })}
          </div>
        </div>
        <details className="rounded-lg border border-surface-700 bg-surface-800/60 p-3">
          <summary className="cursor-pointer text-sm font-medium text-accent-400">Per-platform text</summary>
          <div className="mt-3 space-y-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-zinc-400">YouTube title</label>
              <input className="input-field" value={form.youtube_title} onChange={update('youtube_title')} maxLength={100} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-zinc-400">Instagram caption</label>
              <textarea className="input-field" value={form.instagram_caption} onChange={update('instagram_caption')} maxLength={2200} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-zinc-400">TikTok caption</label>
              <textarea className="input-field" value={form.tiktok_caption} onChange={update('tiktok_caption')} maxLength={2200} />
            </div>
          </div>
        </details>
        <div className="flex justify-end gap-3 pt-2">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving && <Spinner />}
            {post.status === 'pending' ? 'Save changes' : 'Save & re-queue'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
