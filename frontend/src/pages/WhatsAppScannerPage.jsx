import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { whatsappApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';
import Modal from '../components/Modal';
import WhatsAppStatusBadge from '../components/whatsapp/WhatsAppStatusBadge';

function ScoreBadge({ score }) {
  if (score == null) return <span className="text-xs text-zinc-500">—</span>;
  let color = 'text-zinc-400';
  if (score >= 80) color = 'text-green-400';
  else if (score >= 60) color = 'text-yellow-400';
  else if (score >= 30) color = 'text-orange-400';
  else color = 'text-red-400';
  return <span className={`font-mono text-sm font-semibold ${color}`}>{score}/100</span>;
}

function FilterStatus({ status }) {
  const style = status === 'active'
    ? 'bg-green-500/10 text-green-300 ring-green-500/25'
    : status === 'paused'
      ? 'bg-yellow-500/10 text-yellow-300 ring-yellow-500/25'
      : 'bg-zinc-500/10 text-zinc-300 ring-zinc-500/25';
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {status || 'draft'}
    </span>
  );
}

function formatDate(value) {
  if (!value) return 'Never';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Never';
  return date.toLocaleString();
}

export default function WhatsAppScannerPage() {
  const { filterId } = useParams();
  const [filterJob, setFilterJob] = useState(null);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState('disconnected');
  // true when DB says connected but the durable profile was wiped (no volume)
  const [reconnectRequired, setReconnectRequired] = useState(false);
  const [messages, setMessages] = useState([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [stats, setStats] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [lifecycleLoading, setLifecycleLoading] = useState(false);
  const [showResetModal, setShowResetModal] = useState(false);
  const [resettingMessages, setResettingMessages] = useState(false);
  const [messagePage, setMessagePage] = useState(1);
  const [messageTotal, setMessageTotal] = useState(0);
  const [messageStatusFilter, setMessageStatusFilter] = useState('');

  const pageSize = 20;
  const totalPages = Math.ceil(messageTotal / pageSize);

  useEffect(() => {
    if (!filterId) return;
    let cancelled = false;

    const loadInitial = async () => {
      try {
        setLoading(true);
        const [filterResponse, statusResponse, statsResponse] = await Promise.all([
          whatsappApi.getFilterJob(filterId),
          whatsappApi.getStatus(),
          whatsappApi.getStats(filterId),
        ]);
        if (cancelled) return;
        setFilterJob(filterResponse.data);
        const s = statusResponse.data.status || 'disconnected';
        setStatus(s);
        setReconnectRequired(s === 'connected' && Boolean(statusResponse.data.reconnect_required));
        setStats(statsResponse.data);
      } catch (err) {
        if (!cancelled) toast.error(getErrorMessage(err, 'Failed to load WhatsApp filter'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    loadInitial();
    loadMessages(1, '');
    return () => { cancelled = true; };
  }, [filterId]);

  useEffect(() => {
    if (status !== 'waiting_qr') return undefined;
    const interval = setInterval(loadStatus, 3000);
    return () => clearInterval(interval);
  }, [status]);

  const loadStatus = async () => {
    try {
      const { data } = await whatsappApi.getStatus();
      const s = data.status || 'disconnected';
      setStatus(s);
      setReconnectRequired(s === 'connected' && Boolean(data.reconnect_required));
    } catch {
      // Keep the most recently known connection status during transient errors.
    }
  };

  const loadFilterJob = async () => {
    try {
      const { data } = await whatsappApi.getFilterJob(filterId);
      setFilterJob(data);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to refresh filter details'));
    }
  };

  const loadMessages = async (page = messagePage, statusFilter = messageStatusFilter) => {
    try {
      setMessagesLoading(true);
      const params = { page, page_size: pageSize };
      if (statusFilter) params.status = statusFilter;
      const { data } = await whatsappApi.getMessages(params, filterId);
      setMessages(data.messages || []);
      setMessageTotal(data.total || 0);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load messages'));
    } finally {
      setMessagesLoading(false);
    }
  };

  const loadStats = async () => {
    try {
      const { data } = await whatsappApi.getStats(filterId);
      setStats(data);
    } catch {
      // The details and history can still be used if counters fail to refresh.
    }
  };

  const handleActivate = async () => {
    if (!filterJob?.monitored_groups?.length) {
      toast.error('Edit this filter and select at least one monitored group first');
      return;
    }
    try {
      setLifecycleLoading(true);
      await whatsappApi.activateFilterJob(Number(filterId));
      toast.success(filterJob.status === 'paused' ? 'Filter resumed' : 'Filter started');
      await loadFilterJob();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to start filter'));
    } finally {
      setLifecycleLoading(false);
    }
  };

  const handlePause = async () => {
    try {
      setLifecycleLoading(true);
      await whatsappApi.pauseFilterJob(Number(filterId));
      toast.success('Filter paused');
      await loadFilterJob();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to pause filter'));
    } finally {
      setLifecycleLoading(false);
    }
  };

  const handleTriggerScan = async () => {
    try {
      setScanning(true);
      await whatsappApi.triggerScan(Number(filterId));
      toast.success('Scan triggered. Results will appear shortly.');
      setTimeout(() => {
        loadMessages();
        loadStats();
        loadFilterJob();
      }, 5000);
      setTimeout(() => {
        loadMessages();
        loadStats();
        loadFilterJob();
      }, 15000);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to trigger scan'));
    } finally {
      setScanning(false);
    }
  };

  const handleResetMessages = async () => {
    try {
      setResettingMessages(true);
      const { data } = await whatsappApi.resetFilterMessages(Number(filterId));
      setShowResetModal(false);
      setMessagePage(1);
      setMessageStatusFilter('');
      await Promise.all([loadMessages(1, ''), loadStats(), loadFilterJob()]);
      toast.success(data?.message || 'Scan history and checkpoints reset');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to reset scan history'));
    } finally {
      setResettingMessages(false);
    }
  };

  if (loading) {
    return <div className="flex h-64 items-center justify-center"><Spinner /></div>;
  }

  if (!filterJob) {
    return (
      <div className="mx-auto max-w-4xl rounded-xl border border-surface-700 bg-surface-800 p-8 text-center">
        <p className="text-zinc-300">This WhatsApp filter could not be loaded.</p>
        <Link to="/app/whatsapp-scanner" className="mt-4 inline-block text-sm text-accent-300">Back to filters</Link>
      </div>
    );
  }

  const criteria = [
    filterJob.role && `Role: ${filterJob.role}`,
    filterJob.job_title && `Title: ${filterJob.job_title}`,
    filterJob.experience_level && `Experience: ${filterJob.experience_level}`,
    ...(filterJob.keywords || []).map((keyword) => `#${keyword}`),
  ].filter(Boolean);
  const monitoredGroups = filterJob.monitored_groups || [];

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-start gap-3">
          <Link to="/app/whatsapp-scanner" className="mt-1 text-zinc-500 transition hover:text-zinc-200" aria-label="Back to WhatsApp filters">←</Link>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-bold text-zinc-100">{filterJob.name}</h1>
              <FilterStatus status={filterJob.status} />
            </div>
            <p className="mt-1 text-sm text-zinc-400">Review scan activity, message checkpoints, and matching results.</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={`/app/whatsapp-scanner/jobs/${filterId}/edit`}
            className="inline-flex items-center gap-1.5 rounded-lg border border-accent-500/30 bg-accent-500/10 px-3 py-2 text-sm font-medium text-accent-300 transition hover:bg-accent-500/15"
          >
            <span aria-hidden="true">✎</span> Edit Filter
          </Link>
          {filterJob.status === 'active' ? (
            <button
              onClick={handlePause}
              disabled={lifecycleLoading}
              className="rounded-lg border border-yellow-500/20 bg-yellow-500/10 px-3 py-2 text-sm font-medium text-yellow-300 transition hover:bg-yellow-500/15 disabled:opacity-50"
            >
              {lifecycleLoading ? <Spinner /> : 'Ⅱ Pause'}
            </button>
          ) : (
            <button
              onClick={handleActivate}
              disabled={lifecycleLoading}
              className="rounded-lg bg-green-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-green-500 disabled:opacity-50"
            >
              {lifecycleLoading ? <Spinner /> : filterJob.status === 'paused' ? '▶ Resume' : '▶ Start'}
            </button>
          )}
        </div>
      </header>

      {monitoredGroups.length === 0 && (
        <div className="flex flex-col gap-3 rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-yellow-200">This filter needs a monitored group</p>
            <p className="mt-1 text-xs text-zinc-400">Select one to three groups before starting it.</p>
          </div>
          <Link to={`/app/whatsapp-scanner/jobs/${filterId}/edit`} className="shrink-0 text-sm font-medium text-accent-300 hover:text-accent-200">Edit configuration →</Link>
        </div>
      )}

      <section className="rounded-xl border border-surface-700 bg-surface-800 p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-zinc-100">WhatsApp Connection</h2>
            <WhatsAppStatusBadge status={status} reconnectRequired={reconnectRequired} />
          </div>
          <div className="flex items-center gap-3">
            <button onClick={loadStatus} className="text-sm text-zinc-400 transition hover:text-zinc-200">Refresh status</button>
            {status !== 'connected' && (
              <Link to="/app/account/whatsapp" className="rounded-lg bg-accent-500 px-3 py-2 text-sm font-semibold text-white hover:bg-accent-400">Connect WhatsApp</Link>
            )}
          </div>
        </div>
        <p className="mt-3 text-sm text-zinc-400">
          {status === 'connected'
            ? 'The linked account is ready for scheduled and manual scans.'
            : 'Connect WhatsApp to run this filter. Existing results and checkpoints remain available below.'}
        </p>
      </section>

      <section className="rounded-xl border border-surface-700 bg-surface-800 p-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-zinc-100">Configuration Summary</h2>
            <p className="mt-1 text-sm text-zinc-500">Read-only overview. Use Edit Filter to make changes.</p>
          </div>
          <Link to={`/app/whatsapp-scanner/jobs/${filterId}/edit`} className="shrink-0 text-sm font-medium text-accent-300 hover:text-accent-200">Edit →</Link>
        </div>
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="rounded-lg bg-surface-900 p-3">
            <p className="text-xs text-zinc-500">Monitored groups</p>
            <p className="mt-1 text-lg font-semibold text-zinc-100">{monitoredGroups.length}</p>
          </div>
          <div className="rounded-lg bg-surface-900 p-3">
            <p className="text-xs text-zinc-500">Latest / group</p>
            <p className="mt-1 text-lg font-semibold text-zinc-100">{filterJob.latest_messages_limit || 20}</p>
          </div>
          <div className="rounded-lg bg-surface-900 p-3">
            <p className="text-xs text-zinc-500">Scan interval</p>
            <p className="mt-1 text-lg font-semibold text-zinc-100">{filterJob.interval_hours}h</p>
          </div>
          <div className="rounded-lg bg-surface-900 p-3">
            <p className="text-xs text-zinc-500">Threshold</p>
            <p className="mt-1 text-lg font-semibold text-zinc-100">{filterJob.match_threshold}</p>
          </div>
          <div className="rounded-lg bg-surface-900 p-3">
            <p className="text-xs text-zinc-500">Forward to</p>
            <p className="mt-1 truncate text-sm font-semibold text-zinc-100" title={filterJob.forward_group_name || ''}>{filterJob.forward_group_name || 'Not selected'}</p>
          </div>
        </div>
        {criteria.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {criteria.map((item) => (
              <span key={item} className="rounded-md bg-accent-500/10 px-2.5 py-1 text-xs text-accent-300">{item}</span>
            ))}
          </div>
        )}
      </section>

      <section className="rounded-xl border border-surface-700 bg-surface-800 p-6">
        <div>
          <h2 className="text-lg font-semibold text-zinc-100">Scan Checkpoints</h2>
          <p className="mt-1 text-sm text-zinc-500">
            The latest pulled message ID is stored per group. New scans use it as a high-water mark and do not intentionally go back to older messages.
          </p>
        </div>
        {monitoredGroups.length === 0 ? (
          <p className="mt-5 rounded-lg bg-surface-900 p-5 text-center text-sm text-zinc-500">No monitored groups configured yet.</p>
        ) : (
          <div className="mt-5 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-surface-700 text-xs uppercase text-zinc-500">
                  <th className="pb-2 pr-4 font-medium">Group</th>
                  <th className="pb-2 pr-4 font-medium">Last checked</th>
                  <th className="pb-2 font-medium">Latest pulled message ID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-700">
                {monitoredGroups.map((group) => (
                  <tr key={group.id}>
                    <td className="py-3 pr-4 font-medium text-zinc-200">{group.group_name}</td>
                    <td className="py-3 pr-4 whitespace-nowrap text-zinc-400">{formatDate(group.last_checked_at)}</td>
                    <td className="max-w-md break-all py-3 font-mono text-xs text-zinc-400">
                      {group.last_message_id || <span className="font-sans text-zinc-600">Not scanned yet</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {stats && (
        <section className="rounded-xl border border-surface-700 bg-surface-800 p-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-lg font-semibold text-zinc-100">Scan Stats</h2>
              <p className="mt-1 text-xs text-zinc-500">Last completed scan: {formatDate(filterJob.last_scan_at)}</p>
            </div>
            <button
              onClick={handleTriggerScan}
              disabled={scanning || status !== 'connected' || filterJob.status !== 'active' || monitoredGroups.length === 0}
              title={filterJob.status !== 'active' ? 'Start the filter before running a manual scan' : ''}
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-green-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-green-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {scanning ? <Spinner /> : 'Trigger Manual Scan'}
            </button>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
            {[
              ['Total', stats.total_count, 'text-zinc-100'],
              ['Pending', stats.pending_count, 'text-yellow-400'],
              ['Matched', stats.matched_count, 'text-green-400'],
              ['Rejected', stats.rejected_count, 'text-red-400'],
              ['Forwarded', stats.forwarded_count, 'text-blue-400'],
            ].map(([label, value, color]) => (
              <div key={label} className="rounded-lg bg-surface-900 p-3 text-center">
                <p className={`text-2xl font-bold ${color}`}>{value}</p>
                <p className="text-xs text-zinc-500">{label}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="rounded-xl border border-surface-700 bg-surface-800 p-6">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-lg font-semibold text-zinc-100">Messages</h2>
          <div className="flex items-center gap-3">
            <select
              value={messageStatusFilter}
              onChange={(event) => {
                setMessageStatusFilter(event.target.value);
                setMessagePage(1);
                loadMessages(1, event.target.value);
              }}
              className="rounded-lg border border-surface-700 bg-surface-900 px-3 py-1.5 text-sm text-zinc-100 focus:border-accent-500 focus:outline-none"
            >
              <option value="">All Statuses</option>
              <option value="pending">Pending</option>
              <option value="matched">Matched</option>
              <option value="rejected">Rejected</option>
              <option value="ocr_failed">OCR Failed</option>
            </select>
            <button onClick={() => { loadMessages(); loadStats(); loadFilterJob(); }} className="rounded-lg border border-surface-600 bg-surface-700 px-3 py-1.5 text-sm font-medium text-zinc-300 hover:bg-surface-600">Refresh</button>
            <button
              onClick={() => setShowResetModal(true)}
              disabled={resettingMessages || monitoredGroups.length === 0}
              className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-sm font-medium text-red-300 transition hover:bg-red-500/15 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Reset for testing
            </button>
          </div>
        </div>

        {messagesLoading ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : messages.length === 0 ? (
          <p className="py-8 text-center text-sm text-zinc-500">No messages yet. Start this filter and trigger a scan to pull recent messages.</p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-surface-700 text-left text-xs uppercase text-zinc-500">
                    <th className="pb-2 pr-4 font-medium">Sender</th>
                    <th className="pb-2 pr-4 font-medium">Type</th>
                    <th className="pb-2 pr-4 font-medium">Message</th>
                    <th className="pb-2 pr-4 font-medium">Score</th>
                    <th className="pb-2 pr-4 font-medium">Status</th>
                    <th className="pb-2 font-medium">Forwarded</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-surface-700">
                  {messages.map((message) => (
                    <tr key={message.id} className="hover:bg-surface-750">
                      <td className="max-w-[120px] truncate py-2.5 pr-4 text-zinc-300">{message.sender_name || '—'}</td>
                      <td className="py-2.5 pr-4"><span className={`text-xs font-medium ${message.message_type === 'image' ? 'text-purple-400' : 'text-zinc-400'}`}>{message.message_type}</span></td>
                      <td className="max-w-[300px] truncate py-2.5 pr-4 text-zinc-400">{message.message_text || message.ocr_text || '—'}</td>
                      <td className="py-2.5 pr-4"><ScoreBadge score={message.match_score} /></td>
                      <td className="py-2.5 pr-4">
                        <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                          message.status === 'matched' ? 'bg-green-500/10 text-green-300 ring-green-500/30'
                            : message.status === 'rejected' ? 'bg-red-500/10 text-red-300 ring-red-500/30'
                              : message.status === 'ocr_failed' ? 'bg-yellow-500/10 text-yellow-300 ring-yellow-500/30'
                                : 'bg-zinc-500/10 text-zinc-300 ring-zinc-500/30'
                        }`}>{message.status}</span>
                      </td>
                      <td className="py-2.5">{message.forwarded ? <span className="text-xs text-green-400">✓</span> : <span className="text-xs text-zinc-600">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {totalPages > 1 && (
              <div className="mt-4 flex items-center justify-between">
                <p className="text-xs text-zinc-500">Page {messagePage} of {totalPages} ({messageTotal} total)</p>
                <div className="flex gap-2">
                  <button
                    disabled={messagePage <= 1}
                    onClick={() => {
                      const page = messagePage - 1;
                      setMessagePage(page);
                      loadMessages(page);
                    }}
                    className="rounded border border-surface-700 px-3 py-1 text-xs text-zinc-300 hover:bg-surface-700 disabled:opacity-40"
                  >
                    Previous
                  </button>
                  <button
                    disabled={messagePage >= totalPages}
                    onClick={() => {
                      const page = messagePage + 1;
                      setMessagePage(page);
                      loadMessages(page);
                    }}
                    className="rounded border border-surface-700 px-3 py-1 text-xs text-zinc-300 hover:bg-surface-700 disabled:opacity-40"
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </section>

      <Modal
        open={showResetModal}
        onClose={() => !resettingMessages && setShowResetModal(false)}
        title="Reset scan history for testing?"
      >
        <div className="space-y-4">
          <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4">
            <p className="text-sm font-semibold text-red-300">Stored results and checkpoints will be cleared</p>
            <p className="mt-2 text-xs leading-relaxed text-zinc-400">
              This deletes all scanned, matched, rejected, and forwarded records stored by LinkEasy for this filter. It also resets each monitored group checkpoint so the latest messages can be scanned and forwarded again on your next test.
            </p>
          </div>
          <p className="text-xs leading-relaxed text-zinc-500">
            Original WhatsApp messages and copies already sent to the forwarding group are not deleted. Running another scan may send duplicate copies to the forwarding group.
          </p>
          {filterJob.status === 'active' && (
            <p className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 p-3 text-xs text-yellow-200">
              For predictable tests, pause the filter before resetting if a scan may currently be running, then resume it afterward.
            </p>
          )}
          <div className="flex justify-end gap-3">
            <button
              onClick={() => setShowResetModal(false)}
              disabled={resettingMessages}
              className="rounded-lg border border-surface-700 px-4 py-2 text-sm font-medium text-zinc-300 hover:bg-surface-700 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={handleResetMessages}
              disabled={resettingMessages}
              className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:opacity-50"
            >
              {resettingMessages && <Spinner />}
              {resettingMessages ? 'Resetting...' : 'Clear and reset'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
