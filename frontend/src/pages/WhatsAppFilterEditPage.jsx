import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { whatsappApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import TagInput from '../components/feed/TagInput';
import { Spinner } from '../components/Spinner';
import WhatsAppStatusBadge from '../components/whatsapp/WhatsAppStatusBadge';

function addPendingTags(tags, pending) {
  const next = [...tags];
  String(pending || '')
    .split(/[,;\n]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .forEach((item) => {
      if (!next.some((existing) => existing.toLowerCase() === item.toLowerCase())) {
        next.push(item);
      }
    });
  return next;
}

function mergeGroups(...collections) {
  const groups = new Map();
  collections.flat().filter(Boolean).forEach((group) => {
    if (!group?.group_name) return;
    const existing = groups.get(group.group_name);
    groups.set(group.group_name, {
      ...existing,
      ...group,
      whatsapp_id: group.whatsapp_id || existing?.whatsapp_id || '',
    });
  });
  return Array.from(groups.values());
}

export default function WhatsAppFilterEditPage() {
  const { filterId } = useParams();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('disconnected');
  const [filterJob, setFilterJob] = useState(null);

  const [name, setName] = useState('');
  const [role, setRole] = useState('');
  const [jobTitle, setJobTitle] = useState('');
  const [keywords, setKeywords] = useState([]);
  const [pendingKeyword, setPendingKeyword] = useState('');
  const [experienceLevel, setExperienceLevel] = useState('');
  const [matchThreshold, setMatchThreshold] = useState(60);
  const [intervalHours, setIntervalHours] = useState(1);
  const [latestMessagesLimit, setLatestMessagesLimit] = useState(20);

  const [groups, setGroups] = useState([]);
  const [selectedGroups, setSelectedGroups] = useState([]);
  const [forwardGroup, setForwardGroup] = useState('');
  const [groupsLoading, setGroupsLoading] = useState(false);
  const [groupSearch, setGroupSearch] = useState('');
  const [groupSearchLoading, setGroupSearchLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        setLoading(true);
        const [filterResponse, statusResponse] = await Promise.all([
          whatsappApi.getFilterJob(filterId),
          whatsappApi.getStatus(),
        ]);
        if (cancelled) return;

        const data = filterResponse.data;
        setFilterJob(data);
        setName(data.name || '');
        setRole(data.role || '');
        setJobTitle(data.job_title || '');
        setKeywords(data.keywords || []);
        setExperienceLevel(data.experience_level || '');
        setMatchThreshold(data.match_threshold ?? 60);
        setIntervalHours(data.interval_hours ?? 1);
        setLatestMessagesLimit(data.latest_messages_limit ?? 20);

        const savedMonitored = (data.monitored_groups || []).map((group) => ({
          group_name: group.group_name,
          whatsapp_id: group.whatsapp_id || '',
        }));
        const savedForward = data.forward_group
          ? [{
              group_name: data.forward_group.group_name,
              whatsapp_id: data.forward_group.whatsapp_id || '',
            }]
          : [];
        setSelectedGroups(savedMonitored);
        setGroups(mergeGroups(savedMonitored, savedForward));
        setForwardGroup(data.forward_group_name || '');
        setStatus(statusResponse.data.status || 'disconnected');
      } catch (err) {
        toast.error(getErrorMessage(err, 'Failed to load WhatsApp filter'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => { cancelled = true; };
  }, [filterId]);

  useEffect(() => {
    if (!loading && status === 'connected') loadGroups();
  }, [loading, status, filterId]);

  const loadGroups = async (search = '') => {
    try {
      if (search) setGroupSearchLoading(true);
      else setGroupsLoading(true);
      const { data } = await whatsappApi.getGroups(search, filterId);
      const loadedGroups = data.groups || [];

      setGroups((current) => mergeGroups(current, loadedGroups));
      // Refreshing or searching must not discard unsaved checkbox changes.
      // Enrich the current selection with stable ids from the fresh results.
      setSelectedGroups((current) => {
        const available = mergeGroups(current, loadedGroups);
        const byName = new Map(available.map((group) => [group.group_name, group]));
        return current
          .map((group) => byName.get(group.group_name) || group)
          .slice(0, 3);
      });
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to load WhatsApp groups'));
    } finally {
      setGroupsLoading(false);
      setGroupSearchLoading(false);
    }
  };

  const handleFindGroup = (event) => {
    event?.preventDefault();
    if (status !== 'connected') {
      toast.error('Connect WhatsApp before searching for groups');
      return;
    }
    loadGroups(groupSearch.trim());
  };

  const handleToggleGroup = (group) => {
    setSelectedGroups((current) => {
      const selected = current.some((item) => item.group_name === group.group_name);
      if (selected) {
        return current.filter((item) => item.group_name !== group.group_name);
      }
      if (current.length >= 3) {
        toast.error('You can monitor at most 3 groups with one filter');
        return current;
      }
      return [...current, group];
    });
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    const finalKeywords = addPendingTags(keywords, pendingKeyword);

    if (!name.trim()) return toast.error('Please enter a filter name');
    if (!role.trim() && !jobTitle.trim() && finalKeywords.length === 0) {
      return toast.error('Add a role, job title, or keyword so the filter can match posts');
    }
    if (selectedGroups.length < 1 || selectedGroups.length > 3) {
      return toast.error('Select between 1 and 3 groups to monitor');
    }
    if (!forwardGroup) return toast.error('Please select a forwarding group');

    const latestLimit = Number(latestMessagesLimit);
    if (!Number.isInteger(latestLimit) || latestLimit < 1 || latestLimit > 100) {
      return toast.error('Latest messages per group must be between 1 and 100');
    }

    try {
      setSaving(true);
      const knownGroups = mergeGroups(groups, selectedGroups);
      const groupByName = new Map(knownGroups.map((group) => [group.group_name, group]));

      await whatsappApi.updateFilterJob(Number(filterId), {
        name: name.trim(),
        role: role.trim() || null,
        job_title: jobTitle.trim() || null,
        keywords: finalKeywords.length ? finalKeywords : null,
        experience_level: experienceLevel || null,
        match_threshold: Number(matchThreshold),
        interval_hours: Number(intervalHours),
        latest_messages_limit: latestLimit,
      });

      await whatsappApi.selectGroups({
        filter_id: Number(filterId),
        monitored_group_names: selectedGroups.map((group) => group.group_name),
        monitored_group_ids: selectedGroups.map(
          (group) => groupByName.get(group.group_name)?.whatsapp_id || group.whatsapp_id || ''
        ),
        forward_group_name: forwardGroup,
        forward_group_id: groupByName.get(forwardGroup)?.whatsapp_id || '',
      });

      toast.success('Filter configuration saved');
      navigate(`/app/whatsapp-scanner/jobs/${filterId}`);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to save filter configuration'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="flex h-64 items-center justify-center"><Spinner /></div>;
  }

  if (!filterJob) {
    return (
      <div className="mx-auto max-w-3xl rounded-xl border border-surface-700 bg-surface-800 p-8 text-center">
        <p className="text-zinc-300">This WhatsApp filter could not be loaded.</p>
        <Link to="/app/whatsapp-scanner" className="mt-4 inline-block text-sm text-accent-300">Back to filters</Link>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-start gap-3">
          <Link
            to={`/app/whatsapp-scanner/jobs/${filterId}`}
            className="mt-1 text-zinc-500 transition hover:text-zinc-200"
            aria-label="Back to filter details"
          >
            ←
          </Link>
          <div>
            <h1 className="text-2xl font-bold text-zinc-100">Edit WhatsApp Filter</h1>
            <p className="mt-1 text-sm text-zinc-400">
              Update matching rules, scan limits, and the groups used by this filter.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <WhatsAppStatusBadge status={status} />
          <Link
            to={`/app/whatsapp-scanner/jobs/${filterId}`}
            className="rounded-lg border border-surface-700 px-4 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-700"
          >
            Cancel
          </Link>
          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
          >
            {saving && <Spinner />}
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </div>

      <section className="space-y-5 rounded-xl border border-surface-700 bg-surface-800 p-6">
        <div>
          <h2 className="text-lg font-semibold text-zinc-100">Search Filters</h2>
          <p className="mt-1 text-sm text-zinc-500">Define which job messages count as matches.</p>
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">Filter Name</label>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 focus:border-accent-500 focus:outline-none"
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">Role</label>
            <input
              value={role}
              onChange={(event) => setRole(event.target.value)}
              placeholder="e.g., Software Engineer"
              className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">Job Title</label>
            <input
              value={jobTitle}
              onChange={(event) => setJobTitle(event.target.value)}
              placeholder="e.g., Backend Developer"
              className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent-500 focus:outline-none"
            />
          </div>
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">Keywords</label>
          <TagInput
            tags={keywords}
            onChange={setKeywords}
            onPendingChange={setPendingKeyword}
            placeholder="e.g., remote, python, hiring (comma-separated or Enter)..."
          />
          {pendingKeyword && <p className="mt-1 text-xs text-zinc-500">Pending: {pendingKeyword} (will be saved)</p>}
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">Experience Level</label>
            <select
              value={experienceLevel}
              onChange={(event) => setExperienceLevel(event.target.value)}
              className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 focus:border-accent-500 focus:outline-none"
            >
              <option value="">— Any —</option>
              <option value="entry">Entry</option>
              <option value="mid">Mid</option>
              <option value="senior">Senior</option>
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">Scan Interval (hours)</label>
            <input
              type="number"
              min="0.25"
              max="168"
              step="0.25"
              value={intervalHours}
              onChange={(event) => setIntervalHours(event.target.value)}
              className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 focus:border-accent-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">Latest Messages / Group</label>
            <input
              type="number"
              min="1"
              max="100"
              step="1"
              value={latestMessagesLimit}
              onChange={(event) => setLatestMessagesLimit(event.target.value)}
              className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 focus:border-accent-500 focus:outline-none"
            />
            <p className="mt-1 text-xs text-zinc-500">Maximum pulled in one scan</p>
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">Match Threshold ({matchThreshold})</label>
            <input
              type="range"
              min="0"
              max="100"
              value={matchThreshold}
              onChange={(event) => setMatchThreshold(parseInt(event.target.value, 10))}
              className="mt-2 w-full accent-accent-500"
            />
            <div className="flex justify-between text-xs text-zinc-500"><span>0</span><span>100</span></div>
          </div>
        </div>
      </section>

      <section className="space-y-5 rounded-xl border border-surface-700 bg-surface-800 p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-zinc-100">Select Groups to Monitor</h2>
            <p className="mt-1 text-sm text-zinc-500">Choose between 1 and 3 groups for this filter.</p>
          </div>
          {status === 'connected' && (
            <button
              type="button"
              onClick={() => loadGroups()}
              disabled={groupsLoading}
              className="rounded-lg border border-surface-600 bg-surface-700 px-3 py-2 text-sm font-medium text-zinc-300 hover:bg-surface-600 disabled:opacity-50"
            >
              {groupsLoading ? <Spinner /> : 'Refresh Groups'}
            </button>
          )}
        </div>

        {status !== 'connected' && (
          <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 p-4 text-sm text-yellow-200">
            WhatsApp is disconnected. Saved groups remain available, but connect from the{' '}
            <Link to="/app/account/whatsapp" className="font-medium text-accent-300 hover:text-accent-200">Accounts page</Link>{' '}
            to find different groups.
          </div>
        )}

        {status === 'connected' && (
          <div className="flex gap-2">
            <input
              value={groupSearch}
              onChange={(event) => setGroupSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') handleFindGroup(event);
              }}
              placeholder="Find a chat or group"
              className="min-w-0 flex-1 rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent-500 focus:outline-none"
            />
            <button
              type="button"
              onClick={handleFindGroup}
              disabled={groupSearchLoading}
              className="rounded-lg border border-surface-600 px-3 py-2 text-sm text-zinc-200 hover:bg-surface-700 disabled:opacity-50"
            >
              {groupSearchLoading ? <Spinner /> : 'Find'}
            </button>
          </div>
        )}

        {groupsLoading && groups.length === 0 ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : groups.length === 0 ? (
          <p className="rounded-lg border border-surface-700 bg-surface-900 p-5 text-center text-sm text-zinc-500">
            No groups are available. Connect WhatsApp and refresh this list.
          </p>
        ) : (
          <div className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-surface-700 bg-surface-900 p-2">
            {groups.map((group) => {
              const selected = selectedGroups.some((item) => item.group_name === group.group_name);
              return (
                <label
                  key={group.group_name}
                  className={`flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 transition ${selected ? 'bg-accent-500/10 ring-1 ring-inset ring-accent-500/20' : 'hover:bg-surface-800'}`}
                >
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={() => handleToggleGroup(group)}
                    className="h-4 w-4 rounded border-surface-600 bg-surface-800 text-accent-500 focus:ring-accent-500"
                  />
                  <span className="text-sm text-zinc-200">{group.group_name}</span>
                </label>
              );
            })}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className={`text-xs ${selectedGroups.length >= 1 ? 'text-green-400' : 'text-zinc-500'}`}>
            {selectedGroups.length}/3 selected · at least 1 required
          </p>
          <div className="flex flex-wrap gap-2">
            {selectedGroups.map((group) => (
              <span key={group.group_name} className="rounded-full bg-accent-500/10 px-2.5 py-1 text-xs text-accent-300 ring-1 ring-inset ring-accent-500/20">
                {group.group_name}
              </span>
            ))}
          </div>
        </div>

        <div>
          <label className="mb-2 block text-sm font-medium text-zinc-300">Forward Matches To</label>
          <select
            value={forwardGroup}
            onChange={(event) => setForwardGroup(event.target.value)}
            className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 focus:border-accent-500 focus:outline-none"
          >
            <option value="">— Select a group —</option>
            {groups.map((group) => (
              <option key={group.group_name} value={group.group_name}>{group.group_name}</option>
            ))}
          </select>
        </div>

        <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 p-4">
          <p className="text-sm font-medium text-blue-200">Incremental scanning is enabled automatically</p>
          <p className="mt-1 text-xs leading-relaxed text-zinc-400">
            The newest pulled message ID is saved separately for every monitored group. Future scans start after that checkpoint and never intentionally scan older messages again.
          </p>
        </div>
      </section>

      <div className="flex justify-end gap-3 pb-4">
        <Link
          to={`/app/whatsapp-scanner/jobs/${filterId}`}
          className="rounded-lg border border-surface-700 px-4 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-700"
        >
          Cancel
        </Link>
        <button
          type="submit"
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-lg bg-accent-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
        >
          {saving && <Spinner />}
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>
    </form>
  );
}
