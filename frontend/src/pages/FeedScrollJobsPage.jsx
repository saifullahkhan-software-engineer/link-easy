import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { feedScrollApi } from '../api/endpoints';
import { getUserEmail } from '../api/client';
import { getErrorMessage } from '../api/client';
import FeedScrollJobCard from '../components/feed/FeedScrollJobCard';
import Modal from '../components/Modal';
import { Spinner } from '../components/Spinner';

export default function FeedScrollJobsPage() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [jobToDelete, setJobToDelete] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [now, setNow] = useState(Date.now());
  const tickRef = useRef(null);
  const navigate = useNavigate();
  const ownerEmail = getUserEmail();

  useEffect(() => {
    loadJobs();
  }, []);

  /* Live 1-second tick for all visible active countdowns */
  useEffect(() => {
    clearInterval(tickRef.current);
    if (jobs.some((j) => (j.status === 'active' && j.next_scan_at) || j.status === 'paused')) {
      setNow(Date.now());
      tickRef.current = setInterval(() => setNow(Date.now()), 1000);
    }
    return () => clearInterval(tickRef.current);
  }, [jobs]);

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

  const handleDelete = async (job) => {
    if (!job?.id) return;
    setDeleteLoading(true);
    try {
      const { data } = await feedScrollApi.deleteJob(job.id, ownerEmail);
      toast.success(data?.message || 'Feed scroll job deleted successfully');
      setJobs((jobs) => jobs.filter((j) => j.id !== job.id));
      setJobToDelete(null);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to delete job'));
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
              now={now}
              onPause={handlePause}
              onResume={handleResume}
              onDelete={setJobToDelete}
            />
          ))}
        </div>
      )}

      {/* Delete confirmation modal */}
      <Modal
        open={!!jobToDelete}
        onClose={() => !deleteLoading && setJobToDelete(null)}
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
                Deleting job <span className="font-semibold text-zinc-200">&quot;{jobToDelete?.name}&quot;</span> will permanently remove:
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
              onClick={() => setJobToDelete(null)}
              disabled={deleteLoading}
              className="rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-700 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={() => handleDelete(jobToDelete)}
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
