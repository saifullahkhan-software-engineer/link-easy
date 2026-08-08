import { useEffect, useState, useCallback } from 'react';
import toast from 'react-hot-toast';
import { whatsappApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import TagInput from '../components/feed/TagInput';
import { Spinner } from '../components/Spinner';
import BrowserViewPanel from '../components/live/BrowserViewPanel';
import LiveLogsPanel from '../components/live/LiveLogsPanel';

// ── Helpers ──────────────────────────────────────────────────────────────────

function StatusBadge({ status }) {
  const map = {
    disconnected: { bg: 'bg-red-500/10', text: 'text-red-300', ring: 'ring-red-500/20', label: 'Disconnected' },
    waiting_qr: { bg: 'bg-yellow-500/10', text: 'text-yellow-300', ring: 'ring-yellow-500/20', label: 'Waiting for QR' },
    connected: { bg: 'bg-green-500/10', text: 'text-green-300', ring: 'ring-green-500/20', label: 'Connected' },
    error: { bg: 'bg-red-500/10', text: 'text-red-300', ring: 'ring-red-500/20', label: 'Error' },
  };
  const s = map[status] || map.disconnected;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${s.bg} ${s.text} ${s.ring}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${status === 'connected' ? 'bg-green-400 animate-pulse' : 'bg-current'}`} />
      {s.label}
    </span>
  );
}

function ScoreBadge({ score }) {
  if (score == null) return <span className="text-xs text-zinc-500">—</span>;
  let color = 'text-zinc-400';
  if (score >= 80) color = 'text-green-400';
  else if (score >= 60) color = 'text-yellow-400';
  else if (score >= 30) color = 'text-orange-400';
  else color = 'text-red-400';
  return <span className={`text-sm font-mono font-semibold ${color}`}>{score}/100</span>;
}

export default function WhatsAppScannerPage() {
  // ── Connection state ──
  const [status, setStatus] = useState('disconnected');
  const [connecting, setConnecting] = useState(false);
  const [statusPolling, setStatusPolling] = useState(null);

  // ── Groups ──
  const [groups, setGroups] = useState([]);
  const [groupsLoading, setGroupsLoading] = useState(false);
  const [selectedGroups, setSelectedGroups] = useState([]);
  const [forwardGroup, setForwardGroup] = useState('');
  const [savingGroups, setSavingGroups] = useState(false);

  // ── Filters ──
  const [role, setRole] = useState('');
  const [jobTitle, setJobTitle] = useState('');
  const [keywords, setKeywords] = useState([]);
  const [pendingKeyword, setPendingKeyword] = useState('');
  const [experienceLevel, setExperienceLevel] = useState('');
  const [matchThreshold, setMatchThreshold] = useState(60);
  const [filtersLoading, setFiltersLoading] = useState(false);
  const [savingFilters, setSavingFilters] = useState(false);

  // ── Messages / Stats ──
  const [messages, setMessages] = useState([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [stats, setStats] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [messagePage, setMessagePage] = useState(1);
  const [messageTotal, setMessageTotal] = useState(0);
  const [messageStatusFilter, setMessageStatusFilter] = useState('');

  const pageSize = 20;

  // ── Load initial data ──
  useEffect(() => {
    loadStatus();
    loadFilters();
    loadStats();
    loadMessages();
  }, []);

  // ── Poll status when connecting ──
  useEffect(() => {
    if (status === 'waiting_qr') {
      const interval = setInterval(loadStatus, 3000);
      setStatusPolling(interval);
      return () => clearInterval(interval);
    }
    if (statusPolling) {
      clearInterval(statusPolling);
      setStatusPolling(null);
    }
  }, [status]);

  // ── Data loaders ──

  const loadStatus = async () => {
    try {
      const { data } = await whatsappApi.getStatus();
      setStatus(data.status);
    } catch (err) {
      // Silently ignore — may be network issue
    }
  };

  const loadGroups = async () => {
    try {
      setGroupsLoading(true);
      const { data } = await whatsappApi.getGroups();
      setGroups(data.groups || []);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load groups'));
    } finally {
      setGroupsLoading(false);
    }
  };

  const loadFilters = async () => {
    try {
      setFiltersLoading(true);
      const { data } = await whatsappApi.getFilters();
      setRole(data.role || '');
      setJobTitle(data.job_title || '');
      setKeywords(data.keywords || []);
      setExperienceLevel(data.experience_level || '');
      setMatchThreshold(data.match_threshold ?? 60);
    } catch (err) {
      // Silently ignore
    } finally {
      setFiltersLoading(false);
    }
  };

  const loadMessages = async (p = messagePage, statusFilter = messageStatusFilter) => {
    try {
      setMessagesLoading(true);
      const params = { page: p, page_size: pageSize };
      if (statusFilter) params.status = statusFilter;
      const { data } = await whatsappApi.getMessages(params);
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
      const { data } = await whatsappApi.getStats();
      setStats(data);
    } catch (err) {
      // Silently ignore
    }
  };

  // ── Actions ──

  const handleConnect = async () => {
    try {
      setConnecting(true);
      await whatsappApi.connect();
      toast.success('WhatsApp connection started — scan the QR code');
      setStatus('waiting_qr');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to start connection'));
    } finally {
      setConnecting(false);
    }
  };

  const handleRefreshGroups = () => {
    if (status !== 'connected') {
      toast.error('WhatsApp is not connected');
      return;
    }
    loadGroups();
  };

  const handleSaveGroups = async () => {
    if (selectedGroups.length !== 3) {
      toast.error('Please select exactly 3 groups to monitor');
      return;
    }
    if (!forwardGroup) {
      toast.error('Please select a forward group');
      return;
    }
    try {
      setSavingGroups(true);
      await whatsappApi.selectGroups({
        monitored_group_names: selectedGroups.map((g) => g.group_name),
        monitored_group_ids: selectedGroups.map((g) => g.whatsapp_id || ''),
        forward_group_name: forwardGroup,
        forward_group_id: '',
      });
      toast.success('Groups saved successfully');
      loadStats();
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to save groups'));
    } finally {
      setSavingGroups(false);
    }
  };

  const handleSaveFilters = async () => {
    try {
      setSavingFilters(true);
      await whatsappApi.saveFilters({
        role: role || null,
        job_title: jobTitle || null,
        keywords: keywords.length > 0 ? keywords : null,
        experience_level: experienceLevel || null,
        match_threshold: matchThreshold,
      });
      toast.success('Filters saved');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to save filters'));
    } finally {
      setSavingFilters(false);
    }
  };

  const handleTriggerScan = async () => {
    try {
      setScanning(true);
      await whatsappApi.triggerScan();
      toast.success('Scan triggered!');
      setTimeout(() => {
        loadMessages();
        loadStats();
      }, 5000);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to trigger scan'));
    } finally {
      setScanning(false);
    }
  };

  const handleToggleGroup = (group) => {
    setSelectedGroups((prev) => {
      const exists = prev.some((g) => g.group_name === group.group_name);
      if (exists) {
        return prev.filter((g) => g.group_name !== group.group_name);
      }
      if (prev.length >= 3) {
        toast.error('You can select at most 3 groups to monitor');
        return prev;
      }
      return [...prev, group];
    });
  };

  const totalPages = Math.ceil(messageTotal / pageSize);

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      {/* ── Header ─────────────────────────────────────────────── */}
      <div>
        <h1 className="text-2xl font-bold text-zinc-100">WhatsApp Job Scanner</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Monitor WhatsApp groups for job posts, score them, and forward matches automatically
        </p>
      </div>

      {/* ── Section 1: Connection ───────────────────────────────── */}
      <div className="rounded-xl border border-surface-700 bg-surface-800 p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-zinc-100">WhatsApp Connection</h2>
            <StatusBadge status={status} />
          </div>
          <div className="flex items-center gap-3">
            {status === 'connected' && (
              <button
                onClick={handleRefreshGroups}
                disabled={groupsLoading}
                className="rounded-lg border border-surface-600 bg-surface-700 px-4 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-600"
              >
                {groupsLoading ? <Spinner /> : 'Refresh Groups'}
              </button>
            )}
            <button
              onClick={handleConnect}
              disabled={connecting || status === 'waiting_qr'}
              className="inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
            >
              {connecting ? <Spinner /> : null}
              {status === 'connected' ? 'Reconnect' : 'Connect WhatsApp'}
            </button>
          </div>
        </div>
        {status === 'waiting_qr' && (
          <p className="mt-3 text-sm text-yellow-400">
            The browser is open below — scan the WhatsApp Web QR code with your phone to connect.
            (It streams live from the server; if it isn't showing yet, wait a moment or press Start in
            the Live Browser View.)
          </p>
        )}
      </div>

      {/* ── Section 1.5: Live browser view + API logs ─────────────── */}
      <div className="grid items-stretch gap-6 lg:grid-cols-2">
        <BrowserViewPanel />
        <LiveLogsPanel />
      </div>

      {/* ── Section 2: Group Selection ──────────────────────────── */}
      {status === 'connected' && (
        <div className="rounded-xl border border-surface-700 bg-surface-800 p-6">
          <h2 className="mb-4 text-lg font-semibold text-zinc-100">Select Groups to Monitor</h2>

          {groups.length === 0 && !groupsLoading && (
            <p className="mb-4 text-sm text-zinc-400">
              No groups loaded yet. Click "Refresh Groups" above.
            </p>
          )}

          {groupsLoading && (
            <div className="flex items-center justify-center py-8">
              <Spinner />
            </div>
          )}

          {/* Monitored groups — searchable list */}
          {groups.length > 0 && (
            <div className="mb-6">
              <p className="mb-2 text-sm font-medium text-zinc-400">
                Monitored Groups (select exactly 3):
              </p>
              <div className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-surface-700 bg-surface-900 p-2">
                {groups.map((g) => {
                  const isSelected = selectedGroups.some((sg) => sg.group_name === g.group_name);
                  return (
                    <label
                      key={g.group_name}
                      className={`flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 transition ${
                        isSelected
                          ? 'bg-accent-500/10 ring-1 ring-inset ring-accent-500/20'
                          : 'hover:bg-surface-800'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => handleToggleGroup(g)}
                        className="h-4 w-4 rounded border-surface-600 bg-surface-800 text-accent-500 focus:ring-accent-500"
                      />
                      <span className="text-sm text-zinc-200">{g.group_name}</span>
                    </label>
                  );
                })}
              </div>
              <p className="mt-1 text-xs text-zinc-500">
                {selectedGroups.length}/3 groups selected
              </p>
            </div>
          )}

          {/* Forward group — dropdown */}
          {groups.length > 0 && (
            <div className="mb-4">
              <label className="mb-2 block text-sm font-medium text-zinc-400">
                Forward Matches To:
              </label>
              <select
                value={forwardGroup}
                onChange={(e) => setForwardGroup(e.target.value)}
                className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
              >
                <option value="">— Select a group —</option>
                {groups.map((g) => (
                  <option key={g.group_name} value={g.group_name}>
                    {g.group_name}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="flex justify-end">
            <button
              onClick={handleSaveGroups}
              disabled={savingGroups || selectedGroups.length !== 3 || !forwardGroup}
              className="rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
            >
              {savingGroups ? <Spinner /> : 'Save Groups'}
            </button>
          </div>
        </div>
      )}

      {/* ── Section 3: Search Filters ───────────────────────────── */}
      {status === 'connected' && (
        <div className="rounded-xl border border-surface-700 bg-surface-800 p-6">
          <h2 className="mb-4 text-lg font-semibold text-zinc-100">Search Filters</h2>

          {filtersLoading ? (
            <div className="flex items-center justify-center py-4">
              <Spinner />
            </div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                {/* Role */}
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-zinc-300">Role</label>
                  <input
                    type="text"
                    value={role}
                    onChange={(e) => setRole(e.target.value)}
                    placeholder="e.g., Software Engineer"
                    className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
                  />
                </div>

                {/* Job Title */}
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-zinc-300">Job Title</label>
                  <input
                    type="text"
                    value={jobTitle}
                    onChange={(e) => setJobTitle(e.target.value)}
                    placeholder="e.g., Backend Developer"
                    className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
                  />
                </div>
              </div>

              {/* Keywords */}
              <div>
                <label className="mb-1.5 block text-sm font-medium text-zinc-300">Keywords</label>
                <TagInput
                  tags={keywords}
                  onChange={setKeywords}
                  onPendingChange={setPendingKeyword}
                  placeholder="e.g., remote, python, hiring (comma-separated or Enter)..."
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                {/* Experience Level */}
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-zinc-300">
                    Experience Level
                  </label>
                  <select
                    value={experienceLevel}
                    onChange={(e) => setExperienceLevel(e.target.value)}
                    className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
                  >
                    <option value="">— Any —</option>
                    <option value="entry">Entry</option>
                    <option value="mid">Mid</option>
                    <option value="senior">Senior</option>
                  </select>
                </div>

                {/* Match Threshold */}
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-zinc-300">
                    Match Threshold ({matchThreshold})
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    value={matchThreshold}
                    onChange={(e) => setMatchThreshold(parseInt(e.target.value))}
                    className="w-full accent-accent-500"
                  />
                  <div className="flex justify-between text-xs text-zinc-500">
                    <span>0</span>
                    <span>100</span>
                  </div>
                </div>
              </div>

              <div className="flex justify-end">
                <button
                  onClick={handleSaveFilters}
                  disabled={savingFilters}
                  className="rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
                >
                  {savingFilters ? <Spinner /> : 'Save Filters'}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Section 4: Stats ────────────────────────────────────── */}
      {status === 'connected' && stats && (
        <div className="rounded-xl border border-surface-700 bg-surface-800 p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-zinc-100">Scan Stats</h2>
            <button
              onClick={handleTriggerScan}
              disabled={scanning}
              className="inline-flex items-center gap-2 rounded-lg bg-green-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-green-500 disabled:opacity-50"
            >
              {scanning ? <Spinner /> : 'Trigger Manual Scan'}
            </button>
          </div>
          <div className="mt-4 grid grid-cols-5 gap-4">
            <div className="rounded-lg bg-surface-900 p-3 text-center">
              <p className="text-2xl font-bold text-zinc-100">{stats.total_count}</p>
              <p className="text-xs text-zinc-500">Total</p>
            </div>
            <div className="rounded-lg bg-surface-900 p-3 text-center">
              <p className="text-2xl font-bold text-yellow-400">{stats.pending_count}</p>
              <p className="text-xs text-zinc-500">Pending</p>
            </div>
            <div className="rounded-lg bg-surface-900 p-3 text-center">
              <p className="text-2xl font-bold text-green-400">{stats.matched_count}</p>
              <p className="text-xs text-zinc-500">Matched</p>
            </div>
            <div className="rounded-lg bg-surface-900 p-3 text-center">
              <p className="text-2xl font-bold text-red-400">{stats.rejected_count}</p>
              <p className="text-xs text-zinc-500">Rejected</p>
            </div>
            <div className="rounded-lg bg-surface-900 p-3 text-center">
              <p className="text-2xl font-bold text-blue-400">{stats.forwarded_count}</p>
              <p className="text-xs text-zinc-500">Forwarded</p>
            </div>
          </div>
        </div>
      )}

      {/* ── Section 5: Messages Table ───────────────────────────── */}
      {status === 'connected' && (
        <div className="rounded-xl border border-surface-700 bg-surface-800 p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-zinc-100">Messages</h2>
            <div className="flex items-center gap-3">
              <select
                value={messageStatusFilter}
                onChange={(e) => {
                  setMessageStatusFilter(e.target.value);
                  setMessagePage(1);
                  loadMessages(1, e.target.value);
                }}
                className="rounded-lg border border-surface-700 bg-surface-900 px-3 py-1.5 text-sm text-zinc-100 focus:border-accent-500 focus:outline-none"
              >
                <option value="">All Statuses</option>
                <option value="pending">Pending</option>
                <option value="matched">Matched</option>
                <option value="rejected">Rejected</option>
                <option value="ocr_failed">OCR Failed</option>
              </select>
              <button
                onClick={() => { loadMessages(); loadStats(); }}
                className="rounded-lg border border-surface-600 bg-surface-700 px-3 py-1.5 text-sm font-medium text-zinc-300 hover:bg-surface-600"
              >
                Refresh
              </button>
            </div>
          </div>

          {messagesLoading ? (
            <div className="flex items-center justify-center py-8">
              <Spinner />
            </div>
          ) : messages.length === 0 ? (
            <p className="py-8 text-center text-sm text-zinc-500">
              No messages yet. Connect WhatsApp and trigger a scan.
            </p>
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
                    {messages.map((msg) => (
                      <tr key={msg.id} className="hover:bg-surface-750">
                        <td className="py-2.5 pr-4 text-zinc-300 max-w-[120px] truncate">
                          {msg.sender_name || '—'}
                        </td>
                        <td className="py-2.5 pr-4">
                          <span className={`text-xs font-medium ${msg.message_type === 'image' ? 'text-purple-400' : 'text-zinc-400'}`}>
                            {msg.message_type}
                          </span>
                        </td>
                        <td className="py-2.5 pr-4 text-zinc-400 max-w-[300px] truncate">
                          {msg.message_text || (msg.ocr_text || '—')}
                        </td>
                        <td className="py-2.5 pr-4">
                          <ScoreBadge score={msg.match_score} />
                        </td>
                        <td className="py-2.5 pr-4">
                          <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                            msg.status === 'matched' ? 'text-green-300 bg-green-500/10 ring-green-500/30' :
                            msg.status === 'rejected' ? 'text-red-300 bg-red-500/10 ring-red-500/30' :
                            msg.status === 'ocr_failed' ? 'text-yellow-300 bg-yellow-500/10 ring-yellow-500/30' :
                            'text-zinc-300 bg-zinc-500/10 ring-zinc-500/30'
                          }`}>
                            {msg.status}
                          </span>
                        </td>
                        <td className="py-2.5">
                          {msg.forwarded ? (
                            <span className="text-xs text-green-400">✓</span>
                          ) : (
                            <span className="text-xs text-zinc-600">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="mt-4 flex items-center justify-between">
                  <p className="text-xs text-zinc-500">
                    Page {messagePage} of {totalPages} ({messageTotal} total)
                  </p>
                  <div className="flex gap-2">
                    <button
                      disabled={messagePage <= 1}
                      onClick={() => {
                        const p = messagePage - 1;
                        setMessagePage(p);
                        loadMessages(p);
                      }}
                      className="rounded border border-surface-700 px-3 py-1 text-xs text-zinc-300 hover:bg-surface-700 disabled:opacity-40"
                    >
                      Previous
                    </button>
                    <button
                      disabled={messagePage >= totalPages}
                      onClick={() => {
                        const p = messagePage + 1;
                        setMessagePage(p);
                        loadMessages(p);
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
        </div>
      )}
    </div>
  );
}
