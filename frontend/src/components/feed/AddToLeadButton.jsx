import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { feedLeadsApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../Spinner';

/* Remembered pool choice + per-session "already saved" marks.  Re-scans of the
 * same post must not look re-addable, so the saved state survives a refresh of
 * the results list within the session. */
const LAST_POOL_KEY = 'le.feedLeads.lastPool';
const SAVED_KEY = 'le.feedLeads.saved';

export function readLastPool() {
  try {
    return localStorage.getItem(LAST_POOL_KEY) || '';
  } catch {
    return '';
  }
}

function rememberPool(poolId) {
  try {
    if (poolId) localStorage.setItem(LAST_POOL_KEY, poolId);
  } catch {}
}

/** Session marks are keyed by pool + profile so the same person can still be
 *  saved into a different feed scroll job's pool. */
export function savedKey(poolId, linkedinUrl) {
  return `${poolId}::${(linkedinUrl || '').toLowerCase()}`;
}

function readSessionSaved() {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(SAVED_KEY) || '[]'));
  } catch {
    return new Set();
  }
}

export function markSessionSaved(poolId, linkedinUrl) {
  try {
    const saved = readSessionSaved();
    saved.add(savedKey(poolId, linkedinUrl));
    sessionStorage.setItem(SAVED_KEY, JSON.stringify([...saved]));
  } catch {}
}

export function isSessionSaved(poolId, linkedinUrl) {
  return readSessionSaved().has(savedKey(poolId, linkedinUrl));
}

/**
 * "Add to Lead" action for a scored post card.
 *
 * Clicking opens a small popover (never a modal) that lets the user pick which
 * Feed Leads list the profile goes into — one list per feed scroll job, the
 * current job pre-selected, last choice remembered — plus an optional label.
 * Saving stages the profile in that pool; it becomes a campaign lead later,
 * from the campaign's "Feed Leads" tab.
 *
 * Props:
 *   profile   { first_name, last_name, linkedin_url, headline }
 *   metadata  { feed_scroll_result_id, source_post_url, matched_score,
 *               matched_criteria, scan_id }
 *   pools     [{ feed_scroll_job_id, name, saved_count }]
 *   currentPoolId  feed scroll job the results page is showing
 *   savedState     'saved' | 'imported' | null — server-known state
 *   onSaved(feedLead)  parent refreshes its pool snapshot
 */
export default function AddToLeadButton({
  profile,
  metadata = {},
  pools = [],
  currentPoolId,
  ownerEmail,
  savedState = null,
  onSaved,
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [localState, setLocalState] = useState(null); // 'saved' after a save in this view
  const [poolId, setPoolId] = useState(currentPoolId || readLastPool() || '');
  const [label, setLabel] = useState('');
  const containerRef = useRef(null);

  const state = localState || savedState;
  const isSaved = state === 'saved' || state === 'imported';
  const canSave = Boolean(profile?.linkedin_url && profile?.first_name && profile?.last_name);

  // Keep the default in sync when the page switches jobs, unless the user
  // already made a choice for this card.
  useEffect(() => {
    if (!open) setPoolId((current) => current || currentPoolId || readLastPool() || '');
  }, [currentPoolId, open]);

  // Close on outside click / Escape — standard popover behaviour.
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) setOpen(false);
    };
    const onKeyDown = (event) => event.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const poolOptions = pools.length
    ? pools
    : currentPoolId
      ? [{ feed_scroll_job_id: currentPoolId, name: 'This scan', saved_count: 0 }]
      : [];
  const selectedPool = poolOptions.find((pool) => pool.feed_scroll_job_id === poolId);
  const poolName = selectedPool?.name || 'Feed Leads';

  async function save() {
    if (!poolId) {
      toast.error('Pick a feed leads list first.');
      return;
    }
    if (!canSave) {
      toast.error('This post has no usable profile name or link.');
      return;
    }
    setBusy(true);
    try {
      const { data } = await feedLeadsApi.save({
        owner_email: ownerEmail,
        feed_scroll_job_id: poolId,
        feed_scroll_result_id: metadata.feed_scroll_result_id || null,
        first_name: profile.first_name,
        last_name: profile.last_name,
        linkedin_url: profile.linkedin_url,
        headline: profile.headline || null,
        label: label.trim() || null,
        source: 'job_feed_scan',
        source_post_url: metadata.source_post_url || null,
        matched_score: metadata.matched_score ?? null,
        matched_criteria: metadata.matched_criteria || null,
        scan_id: metadata.scan_id || null,
      });
      rememberPool(poolId);
      markSessionSaved(poolId, profile.linkedin_url);
      setLocalState('saved');
      setOpen(false);
      toast.success(`Saved to ${poolName} feed leads.`);
      onSaved?.(data);
    } catch (err) {
      // 409 = already waiting in that list. Still flip to the saved state so
      // the card stops looking addable, but say so explicitly.
      if (err?.response?.status === 409) {
        rememberPool(poolId);
        markSessionSaved(poolId, profile.linkedin_url);
        setLocalState('saved');
        setOpen(false);
        toast(`Already in ${poolName} feed leads`, { icon: 'ℹ️' });
        onSaved?.(null);
      } else {
        // Validation/network failure — button stays clickable so they can retry.
        toast.error(getErrorMessage(err, 'Could not save this profile.'));
      }
    } finally {
      setBusy(false);
    }
  }

  if (isSaved) {
    return (
      <span
        className="inline-flex shrink-0 cursor-default items-center gap-1.5 rounded-md bg-emerald-50 px-2.5 py-1.5 text-sm font-semibold text-emerald-700"
        title={
          state === 'imported'
            ? 'Already imported into a campaign from your feed leads'
            : 'Waiting in your feed leads — add it to a campaign from the campaign’s Feed Leads tab'
        }
      >
        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
        </svg>
        {state === 'imported' ? 'In campaign' : 'Added ✓'}
      </span>
    );
  }

  return (
    // The whole post card is clickable ("open post on LinkedIn") and reacts to
    // Enter/Space — keep both away from the popover so typing a label or
    // picking a list never opens LinkedIn.
    <div
      className="relative shrink-0"
      ref={containerRef}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-semibold text-[#0a66c2] transition hover:bg-[#0a66c2]/10"
        title="Save this profile to your feed leads"
      >
        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M18 7.5v3m0 0v3m0-3h3m-3 0h-3m-2.25-4.125a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0ZM3 19.235v-.11a6.375 6.375 0 0 1 12.75 0v.109A12.318 12.318 0 0 1 9.374 21c-2.331 0-4.512-.645-6.374-1.766Z"
          />
        </svg>
        Add to Lead
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Save to feed leads"
          className="absolute bottom-full right-0 z-30 mb-2 w-72 rounded-xl border border-surface-700 bg-surface-900 p-3 text-left shadow-2xl shadow-black/40"
        >
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
            Save to feed leads
          </p>
          <p className="mt-1 truncate text-xs text-zinc-500" title={profile?.linkedin_url}>
            {[profile?.first_name, profile?.last_name].filter(Boolean).join(' ') || 'Unknown profile'}
          </p>

          <label className="input-label mt-3 block" htmlFor="feed-lead-pool">
            List
          </label>
          <select
            id="feed-lead-pool"
            className="input-field w-full cursor-pointer"
            value={poolId}
            onChange={(e) => setPoolId(e.target.value)}
          >
            {poolOptions.length === 0 && <option value="">No feed scroll jobs</option>}
            {poolOptions.map((pool) => (
              <option key={pool.feed_scroll_job_id} value={pool.feed_scroll_job_id}>
                {pool.name}
                {pool.saved_count ? ` (${pool.saved_count} waiting)` : ''}
              </option>
            ))}
          </select>

          <label className="input-label mt-3 block" htmlFor="feed-lead-label">
            Label <span className="normal-case text-zinc-600">(optional)</span>
          </label>
          <input
            id="feed-lead-label"
            className="input-field w-full"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. Backend hires"
            maxLength={120}
          />

          <div className="mt-3 flex items-center gap-2">
            <button type="button" onClick={save} className="btn-primary flex-1 justify-center" disabled={busy}>
              {busy && <Spinner />}
              {busy ? 'Saving…' : 'Save to list'}
            </button>
            <button type="button" onClick={() => setOpen(false)} className="btn-secondary" disabled={busy}>
              Cancel
            </button>
          </div>

          <p className="mt-3 border-t border-surface-700 pt-2 text-[11px] leading-relaxed text-zinc-500">
            Saved profiles wait here — pick them up from a campaign’s{' '}
            <span className="text-zinc-300">Feed Leads</span> tab.
          </p>
          <Link
            to="/app/campaigns/create"
            className="mt-1 inline-flex items-center gap-1 text-xs font-semibold text-accent-400 transition hover:text-accent-300"
          >
            + New Campaign
          </Link>
        </div>
      )}
    </div>
  );
}
