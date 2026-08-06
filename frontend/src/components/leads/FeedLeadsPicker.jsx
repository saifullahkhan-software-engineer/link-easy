import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { campaignsApi, feedLeadsApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../Spinner';

function displayName(feedLead) {
  return [feedLead.first_name, feedLead.last_name].filter(Boolean).join(' ') || '—';
}

function shortUrl(url) {
  return (url || '').replace(/^https?:\/\/(www\.)?/, '');
}

/**
 * Third lead-intake option next to "Add manually" and "Upload CSV".
 *
 * Lists the profiles staged in a Feed Leads pool (one pool per feed scroll
 * job), lets the user multi-select them and imports the selection into this
 * campaign through the shared leads pathway.  Imported entries are consumed,
 * so the list empties as it is used; anything already in the campaign comes
 * back as a duplicate instead of being inserted twice.
 */
export default function FeedLeadsPicker({ campaignId, ownerEmail, onImported }) {
  const [pools, setPools] = useState([]);
  const [poolId, setPoolId] = useState('');
  const [feedLeads, setFeedLeads] = useState([]);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState(() => new Set());

  const loadPools = useCallback(async () => {
    try {
      const { data } = await feedLeadsApi.pools(ownerEmail);
      const list = data || [];
      setPools(list);
      setPoolId((current) => {
        if (current && list.some((pool) => pool.feed_scroll_job_id === current)) return current;
        const firstWithLeads = list.find((pool) => pool.saved_count > 0);
        return (firstWithLeads || list[0])?.feed_scroll_job_id || '';
      });
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load your feed leads lists.'));
    }
  }, [ownerEmail]);

  const loadFeedLeads = useCallback(async () => {
    if (!poolId) {
      setFeedLeads([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const { data } = await feedLeadsApi.list(ownerEmail, { feedScrollJobId: poolId, status: 'saved' });
      setFeedLeads(data || []);
      setSelected(new Set());
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load feed leads.'));
    } finally {
      setLoading(false);
    }
  }, [ownerEmail, poolId]);

  useEffect(() => {
    loadPools();
  }, [loadPools]);

  useEffect(() => {
    loadFeedLeads();
  }, [loadFeedLeads]);

  const allSelected = feedLeads.length > 0 && selected.size === feedLeads.length;
  const selectedPool = useMemo(
    () => pools.find((pool) => pool.feed_scroll_job_id === poolId),
    [pools, poolId]
  );

  function toggle(id) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(feedLeads.map((item) => item.id)));
  }

  async function discard(feedLead) {
    try {
      await feedLeadsApi.remove(feedLead.id, ownerEmail);
      toast.success(`Removed ${displayName(feedLead)} from this list.`);
      await Promise.all([loadFeedLeads(), loadPools()]);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not remove that feed lead.'));
    }
  }

  async function importSelected() {
    if (selected.size === 0) return;
    setImporting(true);
    try {
      const { data } = await campaignsApi.importFeedLeads(campaignId, ownerEmail, [...selected]);
      const added = data?.added?.length || 0;
      const duplicates = data?.duplicates?.length || 0;
      const errors = data?.errors?.length || 0;

      if (added > 0) {
        toast.success(`Added ${added} lead${added === 1 ? '' : 's'} to ${data.campaign_name}.`);
      }
      if (duplicates > 0) {
        toast(
          `${duplicates} ${duplicates === 1 ? 'profile was' : 'profiles were'} already in ${data.campaign_name} leads`,
          { icon: 'ℹ️' }
        );
      }
      if (errors > 0) {
        toast.error(
          `${errors} ${errors === 1 ? 'entry' : 'entries'} could not be imported: ${
            data.errors[0]?.message || 'validation failed'
          }`
        );
      }
      if (added === 0 && duplicates === 0 && errors === 0) {
        toast('Nothing to import.', { icon: 'ℹ️' });
      }

      await Promise.all([loadFeedLeads(), loadPools()]);
      onImported?.(data);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not import the selected feed leads.'));
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* List picker */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[220px] flex-1">
          <label className="input-label" htmlFor="feed-leads-pool">
            Feed leads list
          </label>
          <select
            id="feed-leads-pool"
            className="input-field w-full cursor-pointer"
            value={poolId}
            onChange={(e) => setPoolId(e.target.value)}
          >
            {pools.length === 0 && <option value="">No feed scroll jobs yet</option>}
            {pools.map((pool) => (
              <option key={pool.feed_scroll_job_id} value={pool.feed_scroll_job_id}>
                {pool.name} — {pool.saved_count} waiting
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          onClick={() => Promise.all([loadPools(), loadFeedLeads()])}
          className="btn-secondary"
          disabled={loading || importing}
        >
          Refresh
        </button>
      </div>

      <p className="text-xs text-zinc-500">
        Profiles you saved from Feed Scroll scan results. Selected leads are added to this campaign
        like any CSV or manual lead, and leave the list once imported.
      </p>

      {/* Table */}
      {loading ? (
        <div className="animate-pulse space-y-2">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-10 rounded bg-surface-700/60" />
          ))}
        </div>
      ) : feedLeads.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-surface-600 px-6 py-10 text-center">
          <svg className="mb-3 h-9 w-9 text-zinc-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
          </svg>
          <p className="text-sm font-medium text-zinc-400">
            {pools.length === 0 ? 'No feed scroll jobs yet' : 'This list is empty'}
          </p>
          <p className="mt-1 max-w-sm text-xs text-zinc-600">
            Open a scan under Feed Scroll and use <span className="text-zinc-400">Add to Lead</span>{' '}
            on a post to save that profile here.
          </p>
          <Link to="/app/feed-scroll" className="btn-secondary mt-4">
            Go to Feed Scroll
          </Link>
        </div>
      ) : (
        <div className="rounded-lg border border-surface-700">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-surface-700 bg-surface-800 px-4 py-2">
            <label className="flex cursor-pointer items-center gap-2 text-xs font-medium text-zinc-300">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 cursor-pointer accent-accent-500"
                checked={allSelected}
                onChange={toggleAll}
              />
              Select all ({feedLeads.length})
            </label>
            <span className="text-xs text-zinc-500">{selected.size} selected</span>
          </div>

          <div className="scrollbar-thin max-h-80 overflow-auto">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-surface-850">
                <tr className="text-xs uppercase tracking-wide text-zinc-500">
                  <th className="px-4 py-2 font-medium" />
                  <th className="px-4 py-2 font-medium">Name</th>
                  <th className="px-4 py-2 font-medium">LinkedIn</th>
                  <th className="px-4 py-2 font-medium">Match</th>
                  <th className="px-4 py-2 font-medium">Saved</th>
                  <th className="px-4 py-2 font-medium" />
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-700/70">
                {feedLeads.map((feedLead) => (
                  <tr
                    key={feedLead.id}
                    className={`transition hover:bg-surface-800/60 ${
                      selected.has(feedLead.id) ? 'bg-accent-500/5' : ''
                    }`}
                  >
                    <td className="px-4 py-2.5">
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 cursor-pointer accent-accent-500"
                        checked={selected.has(feedLead.id)}
                        onChange={() => toggle(feedLead.id)}
                        aria-label={`Select ${displayName(feedLead)}`}
                      />
                    </td>
                    <td className="px-4 py-2.5">
                      <p className="font-medium text-zinc-200">{displayName(feedLead)}</p>
                      {feedLead.headline && (
                        <p className="mt-0.5 max-w-[220px] truncate text-xs text-zinc-500">
                          {feedLead.headline}
                        </p>
                      )}
                      {feedLead.label && (
                        <span className="mt-1 inline-block rounded bg-surface-700 px-1.5 py-0.5 text-[10px] text-zinc-400">
                          {feedLead.label}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <a
                        href={feedLead.linkedin_url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="block max-w-[200px] truncate text-accent-400 hover:text-accent-300 hover:underline"
                      >
                        {shortUrl(feedLead.linkedin_url)}
                      </a>
                    </td>
                    <td className="px-4 py-2.5">
                      {feedLead.matched_score != null && (
                        <span className="rounded bg-green-500/10 px-1.5 py-0.5 text-xs font-semibold text-green-300">
                          {Number(feedLead.matched_score).toFixed(1)}
                        </span>
                      )}
                      {feedLead.matched_criteria?.length > 0 && (
                        <p
                          className="mt-1 max-w-[200px] truncate text-[11px] text-zinc-500"
                          title={feedLead.matched_criteria.join(', ')}
                        >
                          {feedLead.matched_criteria.join(', ')}
                        </p>
                      )}
                      {feedLead.source_post_url && (
                        <a
                          href={feedLead.source_post_url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-[11px] text-zinc-500 hover:text-accent-300 hover:underline"
                        >
                          View post
                        </a>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-xs text-zinc-500">
                      {feedLead.created_at
                        ? new Date(feedLead.created_at).toLocaleDateString(undefined, {
                            month: 'short',
                            day: 'numeric',
                          })
                        : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <button
                        type="button"
                        onClick={() => discard(feedLead)}
                        className="rounded-md p-1.5 text-zinc-500 transition hover:bg-red-500/10 hover:text-red-400"
                        title="Remove from this list"
                      >
                        <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                          <path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" />
                        </svg>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={importSelected}
          className="btn-primary"
          disabled={selected.size === 0 || importing}
        >
          {importing && <Spinner />}
          {importing
            ? 'Adding…'
            : `Add ${selected.size > 0 ? `${selected.size} ` : ''}lead${selected.size === 1 ? '' : 's'}`}
        </button>
        {selectedPool && selectedPool.imported_count > 0 && (
          <span className="text-xs text-zinc-500">
            {selectedPool.imported_count} already used from this list
          </span>
        )}
      </div>
    </div>
  );
}
