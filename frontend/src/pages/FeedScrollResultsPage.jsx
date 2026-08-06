import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { feedScrollApi } from '../api/endpoints';
import { getUserEmail, getErrorMessage } from '../api/client';
import ScoredPostCard from '../components/feed/ScoredPostCard';
import Modal from '../components/Modal';
import { Spinner } from '../components/Spinner';

export default function FeedScrollResultsPage() {
  const { jobId } = useParams();
  const ownerEmail = getUserEmail();
  const navigate = useNavigate();

  const [job, setJob] = useState(null);
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanLoading, setScanLoading] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [sortBy, setSortBy] = useState('score'); // 'score' | 'latest'

  useEffect(() => {
    loadData();
  }, [jobId]);

  const loadData = async () => {
    try {
      setLoading(true);
      const [jobRes, resultsRes] = await Promise.all([
        feedScrollApi.getJob(jobId, ownerEmail),
        feedScrollApi.getResults(jobId, ownerEmail),
      ]);
      setJob(jobRes.data);
      // Keep the raw results; sorting is handled by the `displayResults` memo
      // so the user can switch between "latest" and "top score" without refetching.
      setResults(resultsRes.data || []);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load job data'));
    } finally {
      setLoading(false);
    }
  };

  // Derive the sorted list the user sees. "score" sorts by relevance
  // (descending); "latest" sorts by when the post was scanned (newest first).
  const displayResults = useMemo(() => {
    const list = [...results];
    if (sortBy === 'latest') {
      list.sort((a, b) => new Date(b.scanned_at || 0) - new Date(a.scanned_at || 0));
    } else {
      list.sort((a, b) => (b.score || 0) - (a.score || 0));
    }
    return list;
  }, [results, sortBy]);

  const handleTriggerScan = async () => {
    try {
      setScanLoading(true);
      await feedScrollApi.triggerScan(jobId, ownerEmail);
      toast.success('Scan started! Results will appear shortly.');
      // Poll for results after a delay
      setTimeout(() => loadData(), 30000);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to trigger scan'));
    } finally {
      setScanLoading(false);
    }
  };

  const handleActivate = async () => {
    try {
      await feedScrollApi.activateJob(jobId, ownerEmail);
      toast.success('Job activated!');
      loadData();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to activate job'));
    }
  };

  const handlePause = async () => {
    try {
      await feedScrollApi.pauseJob(jobId, ownerEmail);
      toast.success('Job paused');
      loadData();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to pause job'));
    }
  };

  const handleDelete = async () => {
    if (!job?.id) return;
    setDeleteLoading(true);
    try {
      const { data } = await feedScrollApi.deleteJob(job.id, ownerEmail);
      toast.success(data?.message || 'Feed scroll job deleted successfully');
      navigate('/app/feed-scroll');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to delete job'));
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
      {/* Header */}
      <div className="mb-6">
        <Link
          to="/app/feed-scroll"
          className="mb-2 inline-flex items-center gap-1 text-sm text-zinc-400 transition hover:text-zinc-200"
        >
          <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
          </svg>
          Back to Jobs
        </Link>

        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-2xl">{job.mode === 'job_search' ? '🔍' : '📝'}</span>
              <h1 className="text-2xl font-bold text-zinc-100">{job.name}</h1>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-zinc-400">
              <span>Mode: {job.mode === 'job_search' ? 'Job Search' : 'Post Search'}</span>
              <span>•</span>
              <span>Interval: {job.feed_interval_hours}h</span>
              <span>•</span>
              <span className={job.status === 'active' ? 'text-green-400' : job.status === 'paused' ? 'text-yellow-400' : 'text-zinc-400'}>
                {job.status}
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={`/app/feed-scroll/jobs/${job.id}/edit`}
              className="inline-flex items-center gap-1.5 rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-700 hover:text-zinc-100"
              title="Edit keywords, experience, and job titles for the next scan"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10" />
              </svg>
              Edit
            </Link>
            {job.status === 'active' ? (
              <button
                onClick={handlePause}
                className="inline-flex items-center gap-1.5 rounded-lg border border-yellow-500/20 bg-yellow-500/10 px-3 py-2 text-sm font-medium text-yellow-300 transition hover:bg-yellow-500/15"
              >
                <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
                </svg>
                Pause
              </button>
            ) : (
              <button
                onClick={handleActivate}
                className="inline-flex items-center gap-1.5 rounded-lg border border-green-500/20 bg-green-500/10 px-3 py-2 text-sm font-medium text-green-300 transition hover:bg-green-500/15"
              >
                <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
                Activate
              </button>
            )}
            <button
              onClick={handleTriggerScan}
              disabled={scanLoading}
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent-500 px-3 py-2 text-sm font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
            >
              {scanLoading ? (
                <Spinner />
              ) : (
                <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
                </svg>
              )}
              Scan Now
            </button>
            <button
              onClick={() => setShowDeleteModal(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm font-medium text-red-300 transition hover:bg-red-500/15"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0Z" />
              </svg>
              Delete
            </button>
          </div>
        </div>
      </div>

      {/* Scan info */}
      <div className="mb-4 rounded-lg border border-surface-700 bg-surface-800 p-3">
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <span className="text-zinc-400">
            Last scan: {job.last_scanned_at ? new Date(job.last_scanned_at).toLocaleString() : 'Never'}
          </span>
          {job.next_scan_at && job.status === 'active' && (
            <span className="text-zinc-400">
              Next scan: {new Date(job.next_scan_at).toLocaleString()}
            </span>
          )}
        </div>
      </div>

      {/* Search criteria */}
      <div className="mb-5 rounded-xl border border-accent-500/20 bg-accent-500/5 p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-accent-300">Search criteria</h2>
        <div className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          {job.mode === 'post_search' ? (
            <div className="sm:col-span-2 lg:col-span-4">
              <p className="mb-1 text-xs text-zinc-500">Keywords</p>
              <div className="flex flex-wrap gap-1.5">
                {(job.keywords || []).length ? job.keywords.map((item) => <span key={item} className="rounded-md bg-surface-700 px-2 py-1 text-zinc-200">{item}</span>) : <span className="text-zinc-400">No keywords configured</span>}
              </div>
            </div>
          ) : (
            <>
              <div><p className="text-xs text-zinc-500">Job titles</p><p className="mt-1 text-zinc-200">{(job.job_titles || []).join(', ') || 'Any title'}</p></div>
              <div><p className="text-xs text-zinc-500">Skills</p><p className="mt-1 text-zinc-200">{(job.skill_set || []).join(', ') || 'Any skill'}</p></div>
              <div><p className="text-xs text-zinc-500">Experience</p><p className="mt-1 text-zinc-200">{job.experience_min_years ?? 'Any'}–{job.experience_max_years ?? 'any'} years</p></div>
              <div><p className="text-xs text-zinc-500">Keywords</p><p className="mt-1 text-zinc-200">{(job.keywords || []).join(', ') || 'None'}</p></div>
            </>
          )}
        </div>
      </div>

      {/* Results */}
      {results.length === 0 ? (
        <div className="rounded-xl border border-surface-700 bg-surface-800 p-12 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-surface-700">
            <svg className="h-6 w-6 text-zinc-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-zinc-200">No posts found yet</h3>
          <p className="mt-1 text-sm text-zinc-400">
            {job.status !== 'active'
              ? 'Activate this job to start scanning the LinkedIn feed.'
              : 'Click "Scan Now" to trigger a manual scan.'}
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Sort controls */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-400">
                {results.length} {results.length === 1 ? 'Post' : 'Posts'}
              </h2>
              <p className="mt-0.5 text-xs text-zinc-500">
                Up to 20 highest-scoring posts with verified profile and post links.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-zinc-500">Sort by</span>
              <div className="inline-flex rounded-lg border border-surface-700 bg-surface-800 p-0.5">
                <button
                  type="button"
                  onClick={() => setSortBy('latest')}
                  className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                    sortBy === 'latest'
                      ? 'bg-accent-500 text-white'
                      : 'text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v15m0 0-3-3m3 3 3-3M6 3h12" />
                  </svg>
                  Latest
                </button>
                <button
                  type="button"
                  onClick={() => setSortBy('score')}
                  className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                    sortBy === 'score'
                      ? 'bg-accent-500 text-white'
                      : 'text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 17l6-6 4 4 8-8m0 0h-4m4 0v4" />
                  </svg>
                  Top score
                </button>
              </div>
            </div>
          </div>

          {displayResults.map((post, index) => (
            <ScoredPostCard key={post.id} post={post} rank={index + 1} />
          ))}
        </div>
      )}

      {/* Delete confirmation modal */}
      <Modal
        open={showDeleteModal}
        onClose={() => !deleteLoading && setShowDeleteModal(false)}
        title="Delete Feed Scroll Job"
      >
        <div className="space-y-4">
          <div className="flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-4">
            <svg className="h-5 w-5 shrink-0 text-red-400 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
            </svg>
            <div>
              <p className="text-sm font-semibold text-red-300">This action is irreversible</p>
              <p className="mt-1 text-xs text-zinc-400 leading-relaxed">
                Deleting job <span className="font-semibold text-zinc-200">&quot;{job?.name}&quot;</span> will permanently remove:
              </p>
              <ul className="mt-2 space-y-1 text-xs text-zinc-400">
                <li className="flex items-center gap-1.5">
                  <span className="h-1 w-1 rounded-full bg-red-400" />
                  All scanned post results for this job
                </li>
                <li className="flex items-center gap-1.5">
                  <span className="h-1 w-1 rounded-full bg-red-400" />
                  All scan history and match data
                </li>
                <li className="flex items-center gap-1.5">
                  <span className="h-1 w-1 rounded-full bg-red-400" />
                  Any pending scan tasks from the queue
                </li>
              </ul>
            </div>
          </div>

          <div className="flex items-center justify-end gap-3">
            <button
              onClick={() => setShowDeleteModal(false)}
              disabled={deleteLoading}
              className="rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-700 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={handleDelete}
              disabled={deleteLoading}
              className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-500 disabled:opacity-50"
            >
              {deleteLoading && <Spinner />}
              <span>{deleteLoading ? 'Deleting...' : 'Delete Job'}</span>
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
