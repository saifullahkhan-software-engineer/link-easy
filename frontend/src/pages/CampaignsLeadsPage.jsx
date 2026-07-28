import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { campaignsApi, leadsApi, linkedinApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { CampaignStatusBadge } from '../components/Badge';
import { Spinner } from '../components/Spinner';
import ManualLeadForm from '../components/leads/ManualLeadForm';
import CsvUpload from '../components/leads/CsvUpload';
import LeadsTable from '../components/leads/LeadsTable';

const POLL_INTERVAL_MS = 20_000;

const EMPTY_CAMPAIGN_FORM = {
  name: '',
  description: '',
  daily_connection_limit: 15,
  daily_message_limit: 20,
  daily_visit_limit: 80,
  connection_note_template: '',
  message_templates: [''],
};

/* ------------------------- campaign form (left) -------------------------- */
function CampaignForm({ accountEmail, ownerEmail, onCreated, onCancel, creating, setCreating }) {
  const [form, setForm] = useState(EMPTY_CAMPAIGN_FORM);
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  function setTemplate(i, value) {
    setForm((f) => {
      const templates = [...f.message_templates];
      templates[i] = value;
      return { ...f, message_templates: templates };
    });
  }
  const addTemplate = () => setForm((f) => ({ ...f, message_templates: [...f.message_templates, ''] }));
  const removeTemplate = (i) =>
    setForm((f) => ({ ...f, message_templates: f.message_templates.filter((_, idx) => idx !== i) }));

  async function submit(e) {
    e.preventDefault();
    setCreating(true);
    try {
      const payload = {
        account_email: accountEmail,
        name: form.name.trim(),
        description: form.description.trim() || null,
        daily_connection_limit: Number(form.daily_connection_limit) || 15,
        daily_message_limit: Number(form.daily_message_limit) || 20,
        daily_visit_limit: Number(form.daily_visit_limit) || 80,
        connection_note_template: form.connection_note_template.trim() || null,
        message_templates: form.message_templates.map((t) => t.trim()).filter(Boolean),
      };
      const { data } = await campaignsApi.create(ownerEmail, payload);
      onCreated(data);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not create the campaign.'));
    } finally {
      setCreating(false);
    }
  }

  return (
    <form onSubmit={submit} className="card p-5">
      <h2 className="text-base font-semibold text-zinc-100">New campaign</h2>
      <p className="mt-1 text-xs text-zinc-500">
        A campaign owns its leads and its messaging sequence.
      </p>

      <div className="mt-4 space-y-4">
        <div>
          <label className="input-label" htmlFor="c-name">Name</label>
          <input id="c-name" className="input-field" value={form.name} onChange={set('name')} placeholder="Q3 SaaS founders" required maxLength={255} />
        </div>
        <div>
          <label className="input-label" htmlFor="c-desc">Description</label>
          <textarea id="c-desc" className="input-field min-h-[64px] resize-y" value={form.description} onChange={set('description')} placeholder="Who is this campaign targeting and why?" rows={2} />
        </div>

        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="input-label" htmlFor="c-conn">Conn./day</label>
            <input id="c-conn" type="number" min={1} className="input-field" value={form.daily_connection_limit} onChange={set('daily_connection_limit')} />
          </div>
          <div>
            <label className="input-label" htmlFor="c-msg">Msgs/day</label>
            <input id="c-msg" type="number" min={1} className="input-field" value={form.daily_message_limit} onChange={set('daily_message_limit')} />
          </div>
          <div>
            <label className="input-label" htmlFor="c-visit">Visits/day</label>
            <input id="c-visit" type="number" min={1} className="input-field" value={form.daily_visit_limit} onChange={set('daily_visit_limit')} />
          </div>
        </div>

        <div>
          <label className="input-label" htmlFor="c-note">Connection note template</label>
          <textarea
            id="c-note"
            className="input-field min-h-[72px] resize-y"
            value={form.connection_note_template}
            onChange={set('connection_note_template')}
            placeholder={'Hi {{first_name}}, loved your post about…'}
            rows={3}
          />
          <p className="mt-1 text-xs text-zinc-500">
            Use <code className="rounded bg-surface-700 px-1 py-0.5 text-[11px] text-accent-300">{'{{first_name}}'}</code> for personalization.
          </p>
        </div>

        <div>
          <span className="input-label">Message templates</span>
          <div className="space-y-2">
            {form.message_templates.map((tpl, i) => (
              <div key={i} className="flex items-start gap-2">
                <textarea
                  className="input-field min-h-[56px] flex-1 resize-y"
                  value={tpl}
                  onChange={(e) => setTemplate(i, e.target.value)}
                  placeholder={`Follow-up message ${i + 1}…`}
                  rows={2}
                />
                {form.message_templates.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeTemplate(i)}
                    className="mt-2 rounded-md p-1.5 text-zinc-500 transition hover:bg-surface-700 hover:text-red-300"
                    title="Remove template"
                  >
                    <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                      <path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" />
                    </svg>
                  </button>
                )}
              </div>
            ))}
          </div>
          <button type="button" onClick={addTemplate} className="mt-2 text-xs font-medium text-accent-400 transition hover:text-accent-300">
            + Add another message
          </button>
        </div>

        <div className="flex items-center gap-3 border-t border-surface-700 pt-4">
          <button type="submit" className="btn-primary flex-1" disabled={creating}>
            {creating && <Spinner />}
            {creating ? 'Creating…' : 'Create campaign'}
          </button>
          {onCancel && (
            <button type="button" className="btn-secondary" onClick={onCancel} disabled={creating}>
              Cancel
            </button>
          )}
        </div>
      </div>
    </form>
  );
}

/* ------------------------------ main page -------------------------------- */
export default function CampaignsLeadsPage() {
  const { email: ownerEmail } = useAuth();

  const [booting, setBooting] = useState(true);
  const [bootError, setBootError] = useState(null);
  const [account, setAccount] = useState(null);
  const [campaigns, setCampaigns] = useState([]);
  const [selected, setSelected] = useState(null);       // active campaign object
  const [showForm, setShowForm] = useState(false);      // force-show create form
  const [creating, setCreating] = useState(false);

  const [leads, setLeads] = useState([]);
  const [leadsLoading, setLeadsLoading] = useState(false);
  const [leadTab, setLeadTab] = useState('manual');     // 'manual' | 'csv'
  const [starting, setStarting] = useState(false);
  const pollRef = useRef(null);

  /* bootstrap: linkedin account + campaigns */
  const bootstrap = useCallback(async () => {
    setBootError(null);
    try {
      const acct = await linkedinApi.getAccount().catch((err) => {
        if (err?.response?.status === 404) return null;
        throw err;
      });
      setAccount(acct?.data || null);
      // Only load campaigns once we know an account exists (or confirm it doesn't).
      const { data: camps } = await campaignsApi.list(ownerEmail).catch((err) => {
        if (acct) throw err; // real failure listed below
        return { data: [] }; // no account → no campaigns either
      });
      setCampaigns(camps || []);
      if (camps?.length) setSelected((prev) => prev || camps[camps.length - 1]);
      setShowForm(!(camps?.length > 0));
    } catch (err) {
      setBootError(getErrorMessage(err, 'Could not load campaigns — is the backend running?'));
    } finally {
      setBooting(false);
    }
  }, [ownerEmail]);

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  /* leads fetching + polling while campaign is active */
  const fetchLeads = useCallback(
    async (silent = true) => {
      if (!selected?.id) return;
      if (!silent) setLeadsLoading(true);
      try {
        const { data } = await leadsApi.list(selected.id, ownerEmail);
        setLeads(data || []);
      } catch (err) {
        if (!silent) toast.error(getErrorMessage(err, 'Could not load leads.'));
      } finally {
        setLeadsLoading(false);
      }
    },
    [selected?.id, ownerEmail]
  );

  useEffect(() => {
    setLeads([]);
    if (selected?.id) fetchLeads(false);
  }, [selected?.id, fetchLeads]);

  useEffect(() => {
    clearInterval(pollRef.current);
    if (selected?.status === 'active') {
      pollRef.current = setInterval(() => fetchLeads(true), POLL_INTERVAL_MS);
    }
    return () => clearInterval(pollRef.current);
  }, [selected?.status, fetchLeads]);

  function onCampaignCreated(campaign) {
    setCampaigns((c) => [...c, campaign]);
    setSelected(campaign);
    setShowForm(false);
    toast.success(`Campaign "${campaign.name}" created — now add some leads.`);
  }

  async function startCampaign() {
    if (!selected?.id) return;
    setStarting(true);
    try {
      const { data } = await campaignsApi.start(selected.id, ownerEmail);
      toast.success(data?.message || 'Campaign started — leads are being queued.');
      setSelected((s) => ({ ...s, status: 'active' }));
      fetchLeads(true);
    } catch (err) {
      if (err?.response?.status === 409) {
        toast('Campaign is already running.', { icon: 'ℹ️' });
        setSelected((s) => ({ ...s, status: 'active' }));
      } else {
        toast.error(getErrorMessage(err, 'Could not start the campaign.'));
      }
    } finally {
      setStarting(false);
    }
  }

  /* ------------------------------- render -------------------------------- */
  if (booting) {
    return (
      <div className="animate-pulse">
        <div className="h-8 w-72 rounded bg-surface-700" />
        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-10">
          <div className="card h-96 lg:col-span-3" />
          <div className="card h-96 lg:col-span-7" />
        </div>
      </div>
    );
  }

  if (bootError) {
    return (
      <div className="flex max-w-xl flex-col items-start gap-4 pt-8">
        <h1 className="text-2xl font-bold text-zinc-50">Campaigns & Leads</h1>
        <div className="card w-full border-red-500/30 p-6 text-center">
          <p className="text-sm font-medium text-red-300">{bootError}</p>
          <button
            onClick={() => {
              setBooting(true);
              bootstrap();
            }}
            className="btn-secondary mt-4"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!account) {
    return (
      <div className="flex max-w-xl flex-col items-start gap-4 pt-8">
        <h1 className="text-2xl font-bold text-zinc-50">Campaigns & Leads</h1>
        <div className="card w-full border-amber-500/30 p-6">
          <h2 className="font-semibold text-amber-300">Connect a LinkedIn account first</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Campaigns run through your LinkedIn session. Connect an account, then come back here to
            build your first campaign.
          </p>
          <Link to="/app/account" className="btn-primary mt-4">
            Go to LinkedIn Account →
          </Link>
        </div>
      </div>
    );
  }

  const hasSelection = Boolean(selected?.id);

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-zinc-50">Campaigns & Leads</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Create a campaign, attach leads, then start the sequence.
          </p>
        </div>
        {hasSelection && (
          <button
            onClick={startCampaign}
            disabled={starting || selected.status === 'active' || leads.length === 0}
            title={
              selected.status === 'active'
                ? 'Campaign is already running'
                : leads.length === 0
                  ? 'Add at least one lead to start'
                  : 'Start the outreach sequence for all pending leads'
            }
            className={
              leads.length > 0 && selected.status !== 'active'
                ? 'btn-primary'
                : 'btn-secondary opacity-70'
            }
          >
            {starting && <Spinner />}
            {selected.status === 'active'
              ? 'Campaign running'
              : starting
                ? 'Starting…'
                : `Start campaign${leads.length > 0 ? ` (${leads.length} lead${leads.length === 1 ? '' : 's'})` : ''}`}
          </button>
        )}
      </div>

      <div className="mt-6 grid grid-cols-1 items-start gap-6 lg:grid-cols-10">
        {/* ----------------------- LEFT: campaign panel ----------------------- */}
        <div className="lg:col-span-3">
          {showForm || !hasSelection ? (
            <CampaignForm
              accountEmail={account.linkedin_email}
              ownerEmail={ownerEmail}
              onCreated={onCampaignCreated}
              onCancel={campaigns.length > 0 ? () => setShowForm(false) : undefined}
              creating={creating}
              setCreating={setCreating}
            />
          ) : (
            <div className="space-y-4">
              {/* compact summary card once created */}
              <div className="card p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate text-base font-semibold text-zinc-100">{selected.name}</h2>
                    <p className="mt-0.5 text-xs text-zinc-500">via {selected.account_email}</p>
                  </div>
                  <CampaignStatusBadge status={selected.status} />
                </div>
                {selected.description && (
                  <p className="mt-3 line-clamp-3 text-sm text-zinc-400">{selected.description}</p>
                )}
                <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
                  {[
                    [selected.daily_connection_limit ?? 15, 'conn/day'],
                    [selected.daily_message_limit ?? 20, 'msgs/day'],
                    [selected.daily_visit_limit ?? 80, 'visits/day'],
                  ].map(([v, label]) => (
                    <div key={label} className="rounded-lg bg-surface-800 px-2 py-2">
                      <dt className="text-base font-bold text-zinc-100">{v}</dt>
                      <dd className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</dd>
                    </div>
                  ))}
                </dl>
                <button
                  onClick={() => setShowForm(true)}
                  className="mt-4 text-xs font-medium text-accent-400 transition hover:text-accent-300"
                >
                  Change campaign / create new →
                </button>
              </div>

              {campaigns.length > 1 && (
                <div className="card p-4">
                  <p className="input-label mb-2">Your campaigns</p>
                  <div className="scrollbar-thin max-h-48 space-y-1 overflow-auto">
                    {campaigns.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => setSelected(c)}
                        className={`flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${
                          c.id === selected.id
                            ? 'bg-accent-500/10 text-accent-300 ring-1 ring-inset ring-accent-500/20'
                            : 'text-zinc-400 hover:bg-surface-800 hover:text-zinc-200'
                        }`}
                      >
                        <span className="truncate">{c.name}</span>
                        <CampaignStatusBadge status={c.status} />
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ----------------------- RIGHT: leads panel ------------------------ */}
        <div className="relative lg:col-span-7">
          <div className={`card transition ${!hasSelection ? 'pointer-events-none select-none opacity-40 blur-[0.5px]' : ''}`}>
            {/* tab header */}
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-surface-700 px-5 pt-4">
              <div className="flex gap-1" role="tablist" aria-label="Add leads">
                {[
                  ['manual', 'Add manually'],
                  ['csv', 'Upload CSV'],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    role="tab"
                    aria-selected={leadTab === key}
                    onClick={() => setLeadTab(key)}
                    className={`rounded-t-lg border-b-2 px-4 py-2.5 text-sm font-medium transition ${
                      leadTab === key
                        ? 'border-accent-400 text-accent-300'
                        : 'border-transparent text-zinc-500 hover:text-zinc-300'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2 pb-2">
                <span className="text-xs text-zinc-500">
                  {leads.length} lead{leads.length === 1 ? '' : 's'}
                </span>
                <button
                  onClick={() => fetchLeads(false)}
                  className="rounded-md p-1.5 text-zinc-500 transition hover:bg-surface-700 hover:text-zinc-200"
                  title="Refresh leads"
                >
                  <svg className={`h-4 w-4 ${leadsLoading ? 'animate-spin' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
                  </svg>
                </button>
              </div>
            </div>

            {/* tab body */}
            <div className="p-5">
              {leadTab === 'manual' ? (
                <ManualLeadForm
                  campaignId={selected?.id}
                  ownerEmail={ownerEmail}
                  onLeadAdded={() => fetchLeads(true)}
                />
              ) : (
                <CsvUpload
                  campaignId={selected?.id}
                  ownerEmail={ownerEmail}
                  onUploaded={() => fetchLeads(false)}
                />
              )}
            </div>

            {/* leads table */}
            <div className="border-t border-surface-700">
              <LeadsTable leads={leads} loading={leadsLoading} />
            </div>
          </div>

          {/* locked overlay */}
          {!hasSelection && (
            <div className="absolute inset-0 z-10 flex items-start justify-center pt-28">
              <div className="card flex items-center gap-3 border-amber-500/30 bg-surface-900/95 px-5 py-4 shadow-xl">
                <svg className="h-5 w-5 shrink-0 text-amber-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
                </svg>
                <div>
                  <p className="text-sm font-semibold text-zinc-100">Create a campaign first</p>
                  <p className="text-xs text-zinc-500">Leads must belong to a campaign — use the form on the left.</p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
