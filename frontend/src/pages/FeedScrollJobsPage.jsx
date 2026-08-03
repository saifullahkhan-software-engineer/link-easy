import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { feedScrollApi } from '../api/endpoints';
import { getUserEmail } from '../api/client';
import { getErrorMessage } from '../api/client';
import FeedScrollJobCard from '../components/feed/FeedScrollJobCard';
import { Spinner } from '../components/Spinner';

export default function FeedScrollJobsPage() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();
  const ownerEmail = getUserEmail();

  useEffect(() => {
    loadJobs();
  }, []);

  const loadJobs = async () => {
    try {
      setLoading(true);
      const { data } = await feedScrollApi.listJobs(ownerEmail);
      setJobs(data);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load feed scroll jobs'));
    } finally {
      setLoading(false);
    }
  };

  const handlePause = async (jobId) => {
    try {
      await feedScrollApi.pauseJob(jobId, ownerEmail);
      toast.success('Job paused');
      loadJobs();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to pause job'));
    }
  };

  const handleResume = async (jobId) => {
    try {
      await feedScrollApi.activateJob(jobId, ownerEmail);
      toast.success('Job activated');
      loadJobs();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to resume job'));
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
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100">Feed Scroll Jobs</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Automatically scan LinkedIn feed and find relevant posts
          </p>
        </div>
        <Link
          to="/app/feed-scroll/create"
          className="inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-accent-400"
        >
          <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          New Job
        </Link>
      </div>

      {jobs.length === 0 ? (
        <div className="rounded-xl border border-surface-700 bg-surface-800 p-12 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-surface-700">
            <svg className="h-6 w-6 text-zinc-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 7.5h1.5m-1.5 3h1.5m-7.5 3h7.5m-7.5 3h7.5m3-9h3.375c.621 0 1.125.504 1.125 1.125V18a2.25 2.25 0 0 1-2.25 2.25M16.5 7.5V18a2.25 2.25 0 0 0 2.25 2.25M16.5 7.5V4.875c0-.621-.504-1.125-1.125-1.125H4.125C3.504 3.75 3 4.254 3 4.875V18a2.25 2.25 0 0 0 2.25 2.25h13.5M6 7.5h3v3H6v-3Z" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-zinc-200">No feed scroll jobs yet</h3>
          <p className="mt-1 text-sm text-zinc-400">
            Create your first job to start scanning the LinkedIn feed automatically.
          </p>
          <Link
            to="/app/feed-scroll/create"
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400"
          >
            Create Job
          </Link>
        </div>
      ) : (
        <div className="space-y-4">
          {jobs.map((job) => (
            <FeedScrollJobCard
              key={job.id}
              job={job}
              onPause={handlePause}
              onResume={handleResume}
            />
          ))}
        </div>
      )}
    </div>
  );
}
