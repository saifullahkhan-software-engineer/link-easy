import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { feedScrollApi } from '../api/endpoints';
import { getUserEmail, getErrorMessage } from '../api/client';
import ScoredPostCard from '../components/feed/ScoredPostCard';
import { Spinner } from '../components/Spinner';

export default function FeedScrollResultsPage() {
  const { jobId } = useParams();
  const ownerEmail = getUserEmail();

  const [job, setJob] = useState(null);
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanLoading, setScanLoading] = useState(false);

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
      setResults(resultsRes.data);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load job data'));
    } finally {
      setLoading(false);
    }
  };

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

          <div className="flex items-center gap-2">
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
          {results.map((post, index) => (
            <ScoredPostCard key={post.id} post={post} rank={index + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
