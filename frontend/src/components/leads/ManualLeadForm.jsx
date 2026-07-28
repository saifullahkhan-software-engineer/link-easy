import { useState } from 'react';
import toast from 'react-hot-toast';
import { leadsApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../Spinner';

export const LINKEDIN_URL_PREFIX = 'https://www.linkedin.com/in/';

/** Inline manual lead entry form. Validates the LinkedIn URL client-side
 *  because the backend hard-rejects anything outside /in/. */
export default function ManualLeadForm({ campaignId, ownerEmail, onLeadAdded }) {
  const [form, setForm] = useState({ first_name: '', last_name: '', linkedin_url: '', headline: '' });
  const [busy, setBusy] = useState(false);
  const [urlError, setUrlError] = useState(null);

  const set = (key) => (e) => {
    setForm((f) => ({ ...f, [key]: e.target.value }));
    if (key === 'linkedin_url') setUrlError(null);
  };

  function validateUrl() {
    const url = form.linkedin_url.trim();
    if (!url.startsWith(LINKEDIN_URL_PREFIX)) {
      setUrlError(`URL must start with ${LINKEDIN_URL_PREFIX}`);
      return false;
    }
    return true;
  }

  async function submit(e) {
    e.preventDefault();
    if (!validateUrl()) return;
    setBusy(true);
    try {
      const { data } = await leadsApi.add({
        owner_email: ownerEmail,
        campaign_id: campaignId,
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        linkedin_url: form.linkedin_url.trim(),
        headline: form.headline.trim() || null,
      });
      toast.success(`${data.first_name || 'Lead'} added to the campaign.`);
      setForm({ first_name: '', last_name: '', linkedin_url: '', headline: '' });
      onLeadAdded?.(data);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not add the lead.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="input-label" htmlFor="lead-first">First name</label>
          <input id="lead-first" className="input-field" value={form.first_name} onChange={set('first_name')} required />
        </div>
        <div>
          <label className="input-label" htmlFor="lead-last">Last name</label>
          <input id="lead-last" className="input-field" value={form.last_name} onChange={set('last_name')} required />
        </div>
      </div>
      <div>
        <label className="input-label" htmlFor="lead-url">LinkedIn profile URL</label>
        <input
          id="lead-url"
          type="url"
          className={`input-field ${urlError ? 'border-red-500 focus:border-red-500 focus:ring-red-500/30' : ''}`}
          value={form.linkedin_url}
          onChange={set('linkedin_url')}
          onBlur={() => form.linkedin_url && validateUrl()}
          placeholder="https://www.linkedin.com/in/janedoe"
          required
        />
        {urlError ? (
          <p className="mt-1 text-xs text-red-400">{urlError}</p>
        ) : (
          <p className="mt-1 text-xs text-zinc-500">Personal profile URLs only (…/in/&lt;slug&gt;).</p>
        )}
      </div>
      <div>
        <label className="input-label" htmlFor="lead-headline">
          Headline <span className="normal-case text-zinc-600">(optional)</span>
        </label>
        <input
          id="lead-headline"
          className="input-field"
          value={form.headline}
          onChange={set('headline')}
          placeholder="VP of Sales at Acme"
        />
      </div>
      <button type="submit" className="btn-primary" disabled={busy}>
        {busy && <Spinner />}
        {busy ? 'Adding…' : 'Add lead'}
      </button>
    </form>
  );
}
