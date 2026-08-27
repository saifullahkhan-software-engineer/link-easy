import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { whatsappApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import WhatsAppFilterCard from '../components/whatsapp/WhatsAppFilterCard';
import Modal from '../components/Modal';
import { Spinner } from '../components/Spinner';
import SchedulingDisabledNotice from '../components/SchedulingDisabledNotice';

export default function WhatsAppFilterJobsPage() {
  const [filters, setFilters] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filterToDelete, setFilterToDelete] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [now, setNow] = useState(Date.now());
  const tickRef = useRef(null);

  useEffect(() => {
    loadFilters();
  }, []);

  useEffect(() => {
    clearInterval(tickRef.current);
    if (filters.some((filter) => filter.status === 'active' || filter.status === 'paused')) {
      setNow(Date.now());
      tickRef.current = setInterval(() => setNow(Date.now()), 1000);
    }
    return () => clearInterval(tickRef.current);
  }, [filters]);

  const loadFilters = async () => {
    try {
      setLoading(true);
      const { data } = await whatsappApi.listFilterJobs();
      setFilters(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load WhatsApp filters'));
    } finally {
      setLoading(false);
    }
  };

  const handlePause = async (filterId) => {
    try {
      await whatsappApi.pauseFilterJob(filterId);
      toast.success('Filter paused');
      await loadFilters();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to pause filter'));
    }
  };

  const handleResume = async (filterId) => {
    try {
      await whatsappApi.activateFilterJob(filterId);
      toast.success('Filter started');
      await loadFilters();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to start filter'));
    }
  };

  const handleDelete = async (filter) => {
    if (!filter?.id) return;
    try {
      setDeleteLoading(true);
      const { data } = await whatsappApi.deleteFilterJob(filter.id);
      toast.success(data?.message || 'WhatsApp filter deleted successfully');
      setFilters((current) => current.filter((item) => item.id !== filter.id));
      setFilterToDelete(null);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to delete filter'));
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
    <div className="mx-auto max-w-5xl">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100">WhatsApp Filters</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Create filters for WhatsApp job posts and manage their scan schedules.
          </p>
        </div>
        <Link
          to="/app/whatsapp-scanner/create"
          className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-accent-500 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-accent-400"
        >
          <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          New Filter
        </Link>
      </div>

      <SchedulingDisabledNotice className="mb-6" />

      {filters.length === 0 ? (
        <div className="rounded-xl border border-surface-700 bg-surface-800 p-12 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-surface-700 text-xl">
            💬
          </div>
          <h3 className="text-lg font-medium text-zinc-200">No WhatsApp filters yet</h3>
          <p className="mt-1 text-sm text-zinc-400">
            Create your first filter to scan WhatsApp groups for matching job posts.
          </p>
          <Link
            to="/app/whatsapp-scanner/create"
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400"
          >
            Create Filter
          </Link>
        </div>
      ) : (
        <div className="space-y-4">
          {filters.map((filter) => (
            <WhatsAppFilterCard
              key={filter.id}
              filter={filter}
              now={now}
              onPause={handlePause}
              onResume={handleResume}
              onDelete={setFilterToDelete}
            />
          ))}
        </div>
      )}

      <Modal
        open={!!filterToDelete}
        onClose={() => !deleteLoading && setFilterToDelete(null)}
        title="Delete WhatsApp Filter"
      >
        <div className="space-y-4">
          <div className="flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-4">
            <svg className="mt-0.5 h-5 w-5 shrink-0 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
            </svg>
            <div>
              <p className="text-sm font-semibold text-red-300">This action is irreversible</p>
              <p className="mt-1 text-xs leading-relaxed text-zinc-400">
                Delete <span className="font-semibold text-zinc-200">&quot;{filterToDelete?.name}&quot;</span> and its scan results, group configuration, and history?
              </p>
            </div>
          </div>
          <div className="flex items-center justify-end gap-3">
            <button
              onClick={() => setFilterToDelete(null)}
              disabled={deleteLoading}
              className="rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-700 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={() => handleDelete(filterToDelete)}
              disabled={deleteLoading}
              className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-500 disabled:opacity-50"
            >
              {deleteLoading && <Spinner />}
              {deleteLoading ? 'Deleting...' : 'Delete Filter'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
