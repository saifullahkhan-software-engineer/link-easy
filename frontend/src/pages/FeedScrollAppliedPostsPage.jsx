import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { feedScrollApi } from '../api/endpoints';
import { getUserEmail, getErrorMessage } from '../api/client';
import ScoreBadge from '../components/feed/ScoreBadge';
import Modal from '../components/Modal';
import { Spinner } from '../components/Spinner';

export default function FeedScrollAppliedPostsPage() {
  const { jobId } = useParams();
  const ownerEmail = getUserEmail();
  const navigate = useNavigate();

  const [job, setJob] = useState(null);
  const [appliedPosts, setAppliedPosts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedIds, setSelectedIds] = useState([]);
  const [postToDelete, setPostToDelete] = useState(null); // single post or 'bulk'
  const [deleteLoading, setDeleteLoading] = useState(false);

  useEffect(() => {
    loadData();
  }, [jobId]);

  const loadData = async () => {
    try {
      setLoading(true);
      const [jobRes, postsRes] = await Promise.all([
        feedScrollApi.getJob(jobId, ownerEmail),
        feedScrollApi.listAppliedPosts(jobId, ownerEmail),
      ]);
      setJob(jobRes.data);
      setAppliedPosts(postsRes.data || []);
      setSelectedIds([]);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load applied posts'));
    } finally {
      setLoading(false);
    }
  };

  // Selection state
  const isAllSelected = useMemo(
    () => appliedPosts.length > 0 && selectedIds.length === appliedPosts.length,
    [appliedPosts.length, selectedIds.length]
  );
  const isPartiallySelected = useMemo(
    () => selectedIds.length > 0 && selectedIds.length < appliedPosts.length,
    [appliedPosts.length, selectedIds.length]
  );

  const toggleSelectAll = () => {
    if (isAllSelected) {
      setSelectedIds([]);
    } else {
      setSelectedIds(appliedPosts.map((p) => p.id));
    }
  };

  const toggleSelectOne = (id) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  };

  // Delete handlers
  const handleDeleteSingle = async (post) => {
    if (!post?.id) return;
    setDeleteLoading(true);
    try {
      await feedScrollApi.deleteAppliedPost(jobId, post.id, ownerEmail);
      toast.success('Applied post removed');
      setAppliedPosts((prev) => prev.filter((p) => p.id !== post.id));
      setSelectedIds((prev) => prev.filter((i) => i !== post.id));
      setPostToDelete(null);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to delete applied post'));
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleBulkDelete = async () => {
    if (selectedIds.length === 0) return;
    setDeleteLoading(true);
    try {
      const { data } = await feedScrollApi.bulkDeleteAppliedPosts(
        jobId,
        ownerEmail,
        selectedIds
      );
      toast.success(data?.message || `${selectedIds.length} applied posts removed`);
      setAppliedPosts((prev) => prev.filter((p) => !selectedIds.includes(p.id)));
      setSelectedIds([]);
      setPostToDelete(null);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to delete selected posts'));
    } finally {
      setDeleteLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (!job) {
    return (
      <div className="text-center text-zinc-400">
        <p>Job not found.</p>
        <Link to="/app/feed-scroll" className="mt-2 text-accent-400 hover:text-accent-300">
          ← Back to jobs
        </Link>
      </div>
    );
  }

  return (
    <div>
      {/* Navigation & Header */}
      <div className="mb-6">
        <div className="mb-2 flex items-center gap-3">
          <Link
            to="/app/feed-scroll"
            className="inline-flex items-center gap-1 text-sm text-zinc-400 transition hover:text-zinc-200"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
            </svg>
            Back to Jobs
          </Link>
          <span className="text-zinc-600">•</span>
          <Link
            to={`/app/feed-scroll/jobs/${job.id}`}
            className="inline-flex items-center gap-1 text-sm text-accent-400 transition hover:text-accent-300"
          >
            View Scanned Results
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
            </svg>
          </Link>
        </div>

        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-2xl">{job.mode === 'job_search' ? '🔍' : '📝'}</span>
              <h1 className="text-2xl font-bold text-zinc-100">{job.name}</h1>
              <span className="rounded-md bg-emerald-500/10 px-2.5 py-0.5 text-xs font-semibold text-emerald-400 ring-1 ring-inset ring-emerald-500/20">
                Applied Posts ({appliedPosts.length})
              </span>
            </div>
            <p className="mt-1 text-sm text-zinc-400">
              Posts you marked as applied. All future feed scans crossmatch against these posts to prevent duplicate opportunities.
            </p>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <Link
              to={`/app/feed-scroll/jobs/${job.id}`}
              className="inline-flex items-center gap-1.5 rounded-lg border border-surface-700 bg-surface-700 px-3 py-2 text-sm font-medium text-zinc-200 transition hover:bg-surface-600 hover:text-zinc-100"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.644C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.43 0 .637C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
              </svg>
              View Results
            </Link>
            <Link
              to={`/app/feed-scroll/jobs/${job.id}/edit`}
              className="inline-flex items-center gap-1.5 rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-700 hover:text-zinc-100"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10" />
              </svg>
              Edit Job
            </Link>
          </div>
        </div>
      </div>

      {/* Main Selection Box Toolbar */}
      {appliedPosts.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-surface-700 bg-surface-800 p-4">
          <div className="flex items-center gap-3">
            <label className="flex cursor-pointer items-center gap-2.5 select-none text-sm font-medium text-zinc-200">
              <input
                type="checkbox"
                checked={isAllSelected}
                ref={(input) => {
                  if (input) input.indeterminate = isPartiallySelected;
                }}
                onChange={toggleSelectAll}
                className="h-4 w-4 rounded border-surface-600 bg-surface-900 text-accent-500 focus:ring-accent-500"
              />
              <span>Select all ({appliedPosts.length} posts)</span>
            </label>

            {selectedIds.length > 0 && (
              <span className="rounded bg-accent-500/10 px-2 py-0.5 text-xs font-semibold text-accent-300">
                Selected {selectedIds.length} of {appliedPosts.length}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            {selectedIds.length > 0 && (
              <>
                <button
                  type="button"
                  onClick={() => setSelectedIds([])}
                  className="rounded-lg border border-surface-700 bg-surface-900 px-3 py-1.5 text-xs font-medium text-zinc-400 transition hover:bg-surface-700 hover:text-zinc-200"
                >
                  Clear selection
                </button>
                <button
                  type="button"
                  onClick={() => setPostToDelete('bulk')}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-red-500"
                >
                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0Z" />
                  </svg>
                  Delete Selected ({selectedIds.length})
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {/* Applied Posts List */}
      {appliedPosts.length === 0 ? (
        <div className="rounded-xl border border-surface-700 bg-surface-800 p-12 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/10">
            <svg className="h-6 w-6 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-zinc-200">No applied posts yet</h3>
          <p className="mt-1 text-sm text-zinc-400">
            Mark posts as &quot;Applied&quot; on the feed scan results page. Once marked, they stay in your applied database and future scans will automatically filter them out.
          </p>
          <Link
            to={`/app/feed-scroll/jobs/${job.id}`}
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400"
          >
            Browse Scanned Posts
          </Link>
        </div>
      ) : (
        <div className="space-y-4">
          {appliedPosts.map((post, idx) => {
            const isSelected = selectedIds.includes(post.id);
            const authorName = [post.author_first_name, post.author_last_name].filter(Boolean).join(' ') || post.author_name || 'LinkedIn Member';
            const profileDisplay = (post.author_profile_url || '').replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');

            return (
              <div
                key={post.id}
                className={`overflow-hidden rounded-xl border transition ${
                  isSelected
                    ? 'border-accent-500 bg-surface-800/95 ring-1 ring-accent-500/50'
                    : 'border-surface-700 bg-surface-850 hover:border-surface-600'
                }`}
              >
                {/* Header bar */}
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-surface-700/60 bg-surface-900/60 px-4 py-2.5">
                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleSelectOne(post.id)}
                      className="h-4 w-4 rounded border-surface-600 bg-surface-900 text-accent-500 focus:ring-accent-500"
                    />
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-surface-700 text-xs font-semibold text-zinc-300">
                      #{idx + 1}
                    </span>
                    <span className="inline-flex items-center gap-1 rounded bg-emerald-500/10 px-2 py-0.5 text-xs font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-500/20">
                      Applied ✓
                    </span>
                    <span className="text-xs text-zinc-500">
                      {post.applied_at ? new Date(post.applied_at).toLocaleString() : 'Applied'}
                    </span>
                  </div>

                  <div className="flex items-center gap-2">
                    {post.score != null && post.score > 0 && <ScoreBadge score={post.score} />}
                    <button
                      type="button"
                      onClick={() => setPostToDelete(post)}
                      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-zinc-400 transition hover:bg-red-500/10 hover:text-red-300"
                      title="Remove from applied posts"
                    >
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0Z" />
                      </svg>
                      Delete
                    </button>
                  </div>
                </div>

                {/* Content body */}
                <div className="bg-white p-4 text-left text-zinc-900">
                  <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h4 className="text-base font-semibold text-zinc-900">{authorName}</h4>
                      {/* Clickable Profile Link */}
                      {post.author_profile_url && (
                        <a
                          href={post.author_profile_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={`Profile: ${post.author_profile_url}`}
                          className="mt-0.5 inline-flex items-center gap-1 text-xs font-medium text-[#0a66c2] hover:underline"
                        >
                          <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M19 3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14m-.5 15.5v-5.3a3.26 3.26 0 0 0-3.26-3.26c-.85 0-1.84.52-2.28 1.3v-1.11h-2.79v8.37h2.79v-4.93c0-.77.62-1.4 1.39-1.4a1.4 1.4 0 0 1 1.4 1.4v4.93h2.75M6.46 8.76a1.6 1.6 0 1 0 0-3.2 1.6 1.6 0 0 0 0 3.2m1.4 9.74v-8.37H5.06v8.37z" />
                          </svg>
                          <span>{profileDisplay || 'LinkedIn Profile'}</span>
                        </a>
                      )}
                    </div>

                    {/* Clickable Post Link & Delete */}
                    <div className="flex items-center gap-2">
                      {post.post_url && (
                        <a
                          href={post.post_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={`Post link: ${post.post_url}`}
                          className="inline-flex items-center gap-1.5 rounded-md border border-[#0a66c2]/30 bg-[#0a66c2]/10 px-3 py-1.5 text-xs font-semibold text-[#0a66c2] transition hover:bg-[#0a66c2]/20"
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

                  {/* Post Text */}
                  <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-zinc-800">
                    {post.post_text || 'No post text available.'}
                  </p>
                </div>

                {/* Matched terms strip */}
                {post.matched_terms && post.matched_terms.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5 border-t border-surface-700 bg-surface-900/40 px-4 py-2.5">
                    <span className="text-xs font-medium text-zinc-400">Matched:</span>
                    {post.matched_terms.map((term, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center gap-1 rounded bg-green-500/10 px-2 py-0.5 text-xs text-green-300 ring-1 ring-inset ring-green-500/20"
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
          })}
        </div>
      )}

      {/* Delete confirmation modal */}
      <Modal
        open={!!postToDelete}
        onClose={() => !deleteLoading && setPostToDelete(null)}
        title={postToDelete === 'bulk' ? 'Delete Selected Applied Posts' : 'Remove Applied Post'}
      >
        <div className="space-y-4">
          <div className="flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-4">
            <svg className="h-5 w-5 shrink-0 text-red-400 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
            </svg>
            <div>
              <p className="text-sm font-semibold text-red-300">Confirm Deletion</p>
              <p className="mt-1 text-xs text-zinc-400 leading-relaxed">
                {postToDelete === 'bulk'
                  ? `Are you sure you want to remove ${selectedIds.length} applied post(s)? If deleted, future scans may surface them again if they match your criteria.`
                  : 'Are you sure you want to remove this post from your applied registry? If removed, future scans will no longer filter it out.'}
              </p>
            </div>
          </div>

          <div className="flex items-center justify-end gap-3">
            <button
              onClick={() => setPostToDelete(null)}
              disabled={deleteLoading}
              className="rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-700 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={postToDelete === 'bulk' ? handleBulkDelete : () => handleDeleteSingle(postToDelete)}
              disabled={deleteLoading}
              className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-500 disabled:opacity-50"
            >
              {deleteLoading && <Spinner />}
              <span>{deleteLoading ? 'Deleting...' : postToDelete === 'bulk' ? `Delete ${selectedIds.length} Posts` : 'Delete Post'}</span>
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
