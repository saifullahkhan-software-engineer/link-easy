import { useState, useEffect, useCallback } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { campaignsApi, linkedinApi, leadsApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { Spinner } from '../components/Spinner';
import ManualLeadForm from '../components/leads/ManualLeadForm';
import CsvUpload from '../components/leads/CsvUpload';
import LeadsTable from '../components/leads/LeadsTable';
import Modal from '../components/Modal';

const EMPTY_CAMPAIGN_FORM = {
  name: '',
  description: '',
  daily_connection_limit: 15,
  daily_message_limit: 20,
  daily_visit_limit: 80,
  connection_note_template: '',
  message_templates: [''],
};

const ACTION_TYPES = [
  {
    type: 'visit_profile',
    label: 'Visit Profile',
    desc: 'Triggers a LinkedIn profile visit notification to get their attention',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.644y M21.998 12c0-.18-.042-.355-.118-.515-.173-.364-.475-.664-.86-.81a1.013 1.013 0 0 1-.644 0L2.036 12.322c-.644-.22-1.127-.788-1.282-1.464" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.644C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.43 0 .637C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
      </svg>
    ),
    colorClasses: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20 hover:bg-cyan-500/15',
    pillClasses: 'bg-cyan-500/10 text-cyan-400 ring-cyan-500/20',
  },
  {
    type: 'like_post',
    label: 'Like Post',
    desc: 'Finds and likes their latest post to increase engagement and trust',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M14.25 9.75 16.5 12l-2.25 2.25m-4.5 0L7.5 12l2.25-2.25M6 20.25h12A2.25 2.25 0 0 0 20.25 18V6A2.25 2.25 0 0 0 18 3.75H6A2.25 2.25 0 0 0 3.75 6v12A2.25 2.25 0 0 0 6 20.25Z" />
      </svg>
    ),
    colorClasses: 'bg-rose-500/10 text-rose-400 border-rose-500/20 hover:bg-rose-500/15',
    pillClasses: 'bg-rose-500/10 text-rose-400 ring-rose-500/20',
  },
  {
    type: 'send_connection',
    label: 'Connection Request',
    desc: 'Sends a standard or personalized connection invitation to your prospect',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M18 7.5v3m0 0v3m0-3h3m-3 0h-3m-2.25-4.125a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0ZM3 19.235v-.11a6.375 6.375 0 0 1 12.75 0v.109A12.318 12.318 0 0 1 9.374 21c-2.331 0-4.512-.645-6.374-1.766Z" />
      </svg>
    ),
    colorClasses: 'bg-blue-500/10 text-blue-400 border-blue-500/20 hover:bg-blue-500/15',
    pillClasses: 'bg-blue-500/10 text-blue-400 ring-blue-500/20',
  },
  {
    type: 'send_message',
    label: 'Send Message',
    desc: 'Sends a personalized LinkedIn message once the lead accepts',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375 3.375 0 1 1-.75 0 .375 3.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375 3.375 0 1 1-.75 0 .375 3.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375 3.375 0 1 1-.75 0 .375 3.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025 10.314 10.314 0 0 1-2.22-3.847C1.58 14.905 1 13.522 1 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
      </svg>
    ),
    colorClasses: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/15',
    pillClasses: 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/20',
  },
];

function formatTotalHours(hours) {
  if (hours === 0) return '0 min';
  if (hours < 1) {
    const mins = Math.round(hours * 60);
    return `${mins} min`;
  }
  if (hours < 24) {
    const hrs = Math.floor(hours);
    const mins = Math.round((hours - hrs) * 60);
    if (mins === 0) return `${hrs} hr${hrs === 1 ? '' : 's'}`;
    return `${hrs}h ${mins}m`;
  }
  const days = Math.floor(hours / 24);
  const remainHrs = Math.round(hours % 24);
  if (remainHrs === 0) return `${days} day${days === 1 ? '' : 's'}`;
  return `${days}d ${remainHrs}h`;
}

export default function CampaignCreatePage() {
  const { email: ownerEmail } = useAuth();
  const navigate = useNavigate();

  const [booting, setBooting] = useState(true);
  const [bootError, setBootError] = useState(null);
  const [account, setAccount] = useState(null);
  const [creating, setCreating] = useState(false);

  // After campaign creation, switch to leads management mode on this page
  const [createdCampaign, setCreatedCampaign] = useState(null);
  const [leads, setLeads] = useState([]);
  const [leadsLoading, setLeadsLoading] = useState(false);
  const [leadTab, setLeadTab] = useState('manual');
  const [now, setNow] = useState(Date.now());

  // Form fields
  const [form, setForm] = useState(EMPTY_CAMPAIGN_FORM);
  
  // Steps state
  // Each step: { id: string/number, step_type: string, delay_val: number, delay_unit: 'minutes' | 'hours' | 'days' }
  const [steps, setSteps] = useState([
    { id: 1, step_type: 'visit_profile', delay_val: 0, delay_unit: 'hours' },
    { id: 2, step_type: 'send_connection', delay_val: 2, delay_unit: 'hours' },
  ]);

  // Inline plus button modal state
  const [showPlusModal, setShowPlusModal] = useState(false);
  const [insertAtIndex, setInsertAtIndex] = useState(null); // null means end of sequence
  const [newStepAction, setNewStepAction] = useState('visit_profile');
  const [newStepDelayVal, setNewStepDelayVal] = useState(24);
  const [newStepDelayUnit, setNewStepDelayUnit] = useState('hours');

  // Live tick for leads table (if needed)
  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(interval);
  }, []);

  // Compute total sequence duration in hours
  const totalDurationHours = steps.reduce((total, step, idx) => {
    if (idx === 0) return total; // First step fires immediately (delay is ignored)
    let hrs = Number(step.delay_val) || 0;
    if (step.delay_unit === 'minutes') hrs = hrs / 60;
    else if (step.delay_unit === 'days') hrs = hrs * 24;
    return total + hrs;
  }, 0);

  // Compute cumulative time for each step (for display)
  const cumulativeHours = steps.reduce((acc, step, idx) => {
    if (idx === 0) {
      acc.push(0);
    } else {
      let hrs = Number(step.delay_val) || 0;
      if (step.delay_unit === 'minutes') hrs = hrs / 60;
      else if (step.delay_unit === 'days') hrs = hrs * 24;
      acc.push(acc[acc.length - 1] + hrs);
    }
    return acc;
  }, []);

  // Load account
  const bootstrap = useCallback(async () => {
    setBootError(null);
    try {
      const acct = await linkedinApi.getAccount().catch((err) => {
        if (err?.response?.status === 404) return null;
        throw err;
      });
      setAccount(acct?.data || null);
    } catch (err) {
      setBootError(getErrorMessage(err, 'Could not load accounts.'));
    } finally {
      setBooting(false);
    }
  }, []);

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  const setFormKey = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

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

  // Handlers for steps
  const appendStepFromSidebar = (stepType) => {
    setSteps((currentSteps) => {
      const maxId = currentSteps.reduce((max, s) => Math.max(max, s.id), 0);
      // Default delays: first step 0 delay, subsequent steps default 24 hours (1 day)
      const delayVal = currentSteps.length === 0 ? 0 : 24;
      const delayUnit = 'hours';
      const newStep = {
        id: maxId + 1,
        step_type: stepType,
        delay_val: delayVal,
        delay_unit: delayUnit,
      };
      toast.success(`Added "${ACTION_TYPES.find(a => a.type === stepType).label}" step`);
      return [...currentSteps, newStep];
    });
  };

  const removeStep = (id) => {
    setSteps((currentSteps) => {
      const filtered = currentSteps.filter((s) => s.id !== id);
      // Ensure the first step has delay 0 if user wants, or keep its original
      return filtered;
    });
    toast.success('Step removed');
  };

  const updateStepDelay = (id, val, unit) => {
    setSteps((currentSteps) =>
      currentSteps.map((s) => (s.id === id ? { ...s, delay_val: Number(val), delay_unit: unit } : s))
    );
  };

  const updateStepType = (id, type) => {
    setSteps((currentSteps) =>
      currentSteps.map((s) => (s.id === id ? { ...s, step_type: type } : s))
    );
  };

  const triggerPlusModal = (index = null) => {
    setInsertAtIndex(index);
    // set sensible defaults
    setNewStepAction('visit_profile');
    setNewStepDelayVal(index === 0 ? 0 : 24);
    setNewStepDelayUnit('hours');
    setShowPlusModal(true);
  };

  const handleAddStepConfirm = () => {
    const maxId = steps.reduce((max, s) => Math.max(max, s.id), 0);
    const newStep = {
      id: maxId + 1,
      step_type: newStepAction,
      delay_val: Number(newStepDelayVal),
      delay_unit: newStepDelayUnit,
    };

    if (insertAtIndex === null) {
      setSteps((current) => [...current, newStep]);
    } else {
      setSteps((current) => {
        const copy = [...current];
        copy.splice(insertAtIndex, 0, newStep);
        return copy;
      });
    }

    setShowPlusModal(false);
    toast.success(`Added "${ACTION_TYPES.find(a => a.type === newStepAction).label}" step`);
  };

  // Submit campaign
  async function submitCampaign(e) {
    e.preventDefault();
    if (steps.length === 0) {
      toast.error('Please add at least one outreach action in your sequence.');
      return;
    }
    setCreating(true);
    try {
      // Map steps to the API schema
      const mappedSteps = steps.map((s, idx) => {
        let delay_hours = Number(s.delay_val) || 0;
        if (s.delay_unit === 'minutes') {
          delay_hours = Number((delay_hours / 60).toFixed(4));
        } else if (s.delay_unit === 'days') {
          delay_hours = delay_hours * 24;
        }

        return {
          step_order: idx + 1,
          step_type: s.step_type,
          delay_hours: delay_hours,
          condition: s.step_type === 'send_message' ? 'accepted' : null,
        };
      });

      const payload = {
        account_email: account.linkedin_email,
        name: form.name.trim(),
        description: form.description.trim() || null,
        daily_connection_limit: Number(form.daily_connection_limit) || 15,
        daily_message_limit: Number(form.daily_message_limit) || 20,
        daily_visit_limit: Number(form.daily_visit_limit) || 80,
        connection_note_template: form.connection_note_template.trim() || null,
        message_templates: form.message_templates.map((t) => t.trim()).filter(Boolean),
        steps: mappedSteps,
      };

      const { data } = await campaignsApi.create(ownerEmail, payload);
      toast.success(`Campaign "${data.name}" successfully created!`);

      // Instead of redirecting, switch to leads management mode on this page
      setCreatedCampaign(data);
      setLeads([]);
      fetchLeadsForCampaign(data.id, false);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not create the campaign.'));
    } finally {
      setCreating(false);
    }
  }

  // Fetch leads for a specific campaign (used after creation)
  const fetchLeadsForCampaign = useCallback(
    async (campaignId, silent = true) => {
      if (!campaignId) return;
      if (!silent) setLeadsLoading(true);
      try {
        const { data } = await leadsApi.list(campaignId, ownerEmail);
        setLeads(data || []);
      } catch (err) {
        if (!silent) toast.error(getErrorMessage(err, 'Could not load leads.'));
      } finally {
        setLeadsLoading(false);
      }
    },
    [ownerEmail]
  );

  // Campaign controls for the newly created campaign (pause, delete, refresh)
  const [statusTransitioning, setStatusTransitioning] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteLoading, setDeleteLoading] = useState(false);

  async function pauseCreatedCampaign() {
    if (!createdCampaign?.id) return;
    setStatusTransitioning(true);
    try {
      const { data } = await campaignsApi.pause(createdCampaign.id, ownerEmail);
      toast.success(data?.message || 'Campaign paused successfully.');
      setCreatedCampaign((prev) => ({ ...prev, status: 'paused' }));
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not pause the campaign.'));
    } finally {
      setStatusTransitioning(false);
    }
  }

  async function restartCreatedCampaign() {
    if (!createdCampaign?.id) return;
    setStatusTransitioning(true);
    try {
      const { data } = await campaignsApi.restart(createdCampaign.id, ownerEmail);
      toast.success(data?.message || 'Campaign restarted successfully.');
      setCreatedCampaign((prev) => ({ ...prev, status: 'active' }));
      fetchLeadsForCampaign(createdCampaign.id, true);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not restart the campaign.'));
    } finally {
      setStatusTransitioning(false);
    }
  }

  async function startCreatedCampaign() {
    if (!createdCampaign?.id) return;
    setStatusTransitioning(true);
    try {
      const { data } = await campaignsApi.start(createdCampaign.id, ownerEmail);
      toast.success(data?.message || 'Campaign started — leads are being queued.');
      setCreatedCampaign((prev) => ({ ...prev, status: 'active' }));
      fetchLeadsForCampaign(createdCampaign.id, true);
    } catch (err) {
      if (err?.response?.status === 409) {
        toast('Campaign is already running.', { icon: 'ℹ️' });
        setCreatedCampaign((prev) => ({ ...prev, status: 'active' }));
      } else {
        toast.error(getErrorMessage(err, 'Could not start the campaign.'));
      }
    } finally {
      setStatusTransitioning(false);
    }
  }

  async function deleteCreatedCampaign() {
    if (!createdCampaign?.id) return;
    setDeleteLoading(true);
    try {
      const { data } = await campaignsApi.delete(createdCampaign.id, ownerEmail);
      toast.success(data?.message || 'Campaign deleted successfully.');
      // Reset to create new campaign mode
      setCreatedCampaign(null);
      setLeads([]);
      setForm(EMPTY_CAMPAIGN_FORM);
      setSteps([
        { id: 1, step_type: 'visit_profile', delay_val: 0, delay_unit: 'hours' },
        { id: 2, step_type: 'send_connection', delay_val: 2, delay_unit: 'hours' },
      ]);
      setShowDeleteModal(false);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not delete the campaign.'));
    } finally {
      setDeleteLoading(false);
    }
  }

  const refreshLeads = () => {
    if (createdCampaign?.id) {
      fetchLeadsForCampaign(createdCampaign.id, false);
    }
  };

  if (booting) {
    return (
      <div className="animate-pulse space-y-6">
        <div className="h-8 w-72 rounded bg-surface-700" />
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          <div className="card h-96 lg:col-span-4" />
          <div className="card h-96 lg:col-span-8" />
        </div>
      </div>
    );
  }

  if (bootError) {
    return (
      <div className="flex max-w-xl flex-col items-start gap-4 pt-8">
        <h1 className="text-2xl font-bold text-zinc-50">Create Campaign</h1>
        <div className="card w-full border-red-500/30 p-6 text-center">
          <p className="text-sm font-medium text-red-300">{bootError}</p>
          <button onClick={() => { setBooting(true); bootstrap(); }} className="btn-secondary mt-4">
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!account) {
    return (
      <div className="flex max-w-xl flex-col items-start gap-4 pt-8">
        <h1 className="text-2xl font-bold text-zinc-50">Create Campaign</h1>
        <div className="card w-full border-amber-500/30 p-6">
          <h2 className="font-semibold text-amber-300">Connect a LinkedIn account first</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Campaigns run through your LinkedIn session. Connect an account, then come back here to
            build your sequence.
          </p>
          <Link to="/app/account" className="btn-primary mt-4">
            Go to LinkedIn Account →
          </Link>
        </div>
      </div>
    );
  }

  // If campaign was just created, show Leads management UI (hide sequence builder)
  if (createdCampaign) {
    return (
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-zinc-50">Campaign Created — Manage Leads</h1>
            <p className="mt-1 text-sm text-zinc-400">
              {createdCampaign.name} • {createdCampaign.description || 'No description'}
            </p>
          </div>

          {/* Action buttons: Refresh, Pause/Start, Delete (instead of latest activity) */}
          <div className="flex items-center gap-3">
            <button
              onClick={refreshLeads}
              disabled={leadsLoading || statusTransitioning}
              className="inline-flex items-center gap-2 rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-sm font-semibold text-zinc-300 transition hover:bg-surface-700"
              title="Refresh leads"
            >
              <svg className={`h-4 w-4 ${leadsLoading ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
              </svg>
              <span>Refresh</span>
            </button>

            {/* Pause / Start / Resume buttons */}
            {createdCampaign.status !== 'active' ? (
              <button
                onClick={createdCampaign.status === 'paused' || createdCampaign.status === 'failed' ? restartCreatedCampaign : startCreatedCampaign}
                disabled={statusTransitioning || leads.length === 0}
                className={`btn-primary inline-flex items-center gap-2 ${leads.length === 0 ? 'opacity-50 cursor-not-allowed' : ''}`}
                title={leads.length === 0 ? 'Add at least one lead to start' : 'Start / Resume campaign'}
              >
                {statusTransitioning && <Spinner />}
                <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 20 20">
                  <path d="M6.3 2.841A1.5 1.5 0 0 0 4 4.11v11.78a1.5 1.5 0 0 0 2.3 1.27l9.344-5.89a1.5 1.5 0 0 0 0-2.538L6.3 2.841Z" />
                </svg>
                <span>
                  {createdCampaign.status === 'paused' || createdCampaign.status === 'failed' ? 'Resume Campaign' : 'Start Campaign'}
                </span>
              </button>
            ) : (
              <button
                onClick={pauseCreatedCampaign}
                disabled={statusTransitioning}
                className="btn-danger inline-flex items-center gap-2"
              >
                {statusTransitioning && <Spinner />}
                <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 20 20">
                  <path d="M5.75 3a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-1.5 0V3.75A.75.75 0 0 1 5.75 3ZM14.25 3a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-1.5 0V3.75a.75.75 0 0 1 .75-.75Z" />
                </svg>
                <span>Pause Campaign</span>
              </button>
            )}

            {/* Delete Button */}
            <button
              onClick={() => setShowDeleteModal(true)}
              disabled={statusTransitioning}
              className="inline-flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-2 text-sm font-semibold text-red-400 transition hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50"
              title="Delete this campaign"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0Z" />
              </svg>
              <span>Delete</span>
            </button>
          </div>
        </div>

        {/* Leads Management Section (replaces Outreach Actions + Drip Sequence Flow) */}
        <div className="card">
          {/* Tab header */}
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
                onClick={refreshLeads}
                className="rounded-md p-1.5 text-zinc-500 transition hover:bg-surface-700 hover:text-zinc-200"
                title="Refresh leads"
              >
                <svg className={`h-4 w-4 ${leadsLoading ? 'animate-spin' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
                </svg>
              </button>
            </div>
          </div>

          {/* Tab body */}
          <div className="p-5">
            {leadTab === 'manual' ? (
              <ManualLeadForm
                campaignId={createdCampaign.id}
                ownerEmail={ownerEmail}
                onLeadAdded={() => fetchLeadsForCampaign(createdCampaign.id, true)}
              />
            ) : (
              <CsvUpload
                campaignId={createdCampaign.id}
                ownerEmail={ownerEmail}
                onUploaded={() => fetchLeadsForCampaign(createdCampaign.id, false)}
              />
            )}
          </div>

          {/* Leads table */}
          <div className="border-t border-surface-700">
            <LeadsTable leads={leads} loading={leadsLoading} steps={[]} now={now} />
          </div>
        </div>

        {/* Delete Confirmation Modal */}
        <Modal
          open={showDeleteModal}
          onClose={() => !deleteLoading && setShowDeleteModal(false)}
          title="Delete Campaign"
        >
          <div className="space-y-4">
            <div className="flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-4">
              <svg className="h-5 w-5 shrink-0 text-red-400 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
              </svg>
              <div>
                <p className="text-sm font-semibold text-red-300">This action is irreversible</p>
                <p className="mt-1 text-xs text-zinc-400 leading-relaxed">
                  Deleting campaign <span className="font-semibold text-zinc-200">"{createdCampaign?.name}"</span> will permanently remove all leads, sequence steps and job history.
                </p>
              </div>
            </div>

            <div className="flex items-center justify-end gap-3">
              <button
                onClick={() => setShowDeleteModal(false)}
                disabled={deleteLoading}
                className="rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-700 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={deleteCreatedCampaign}
                disabled={deleteLoading}
                className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-500 disabled:opacity-50"
              >
                {deleteLoading && <Spinner />}
                <span>{deleteLoading ? 'Deleting...' : 'Delete Campaign'}</span>
              </button>
            </div>
          </div>
        </Modal>
      </div>
    );
  }

  // Default: Show the original Create Campaign form (Outreach Actions + Drip Sequence)
  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-zinc-50">Create Campaign</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Design your visual drip outreach flow, configure thresholds, and launch your automated sequences.
        </p>
      </div>

      <form onSubmit={submitCampaign} className="grid grid-cols-1 items-start gap-6 lg:grid-cols-12">
        {/* Left Column: Basic settings */}
        <div className="space-y-6 lg:col-span-4">
          <div className="card p-5 space-y-4">
            <h2 className="text-base font-semibold text-zinc-100">Campaign Details</h2>

            <div>
              <label className="input-label" htmlFor="c-name">Name</label>
              <input
                id="c-name"
                className="input-field"
                value={form.name}
                onChange={setFormKey('name')}
                placeholder="Q3 Founders Outreach"
                required
                maxLength={255}
              />
            </div>

            <div>
              <label className="input-label" htmlFor="c-desc">Description</label>
              <textarea
                id="c-desc"
                className="input-field min-h-[64px] resize-y"
                value={form.description}
                onChange={setFormKey('description')}
                placeholder="Targeting tech founders in New York..."
                rows={2}
              />
            </div>

            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="input-label" title="Connections per day">Conn./day</label>
                <input
                  type="number"
                  min={1}
                  className="input-field px-2"
                  value={form.daily_connection_limit}
                  onChange={setFormKey('daily_connection_limit')}
                />
              </div>
              <div>
                <label className="input-label" title="Messages per day">Msgs/day</label>
                <input
                  type="number"
                  min={1}
                  className="input-field px-2"
                  value={form.daily_message_limit}
                  onChange={setFormKey('daily_message_limit')}
                />
              </div>
              <div>
                <label className="input-label" title="Visits per day">Visits/day</label>
                <input
                  type="number"
                  min={1}
                  className="input-field px-2"
                  value={form.daily_visit_limit}
                  onChange={setFormKey('daily_visit_limit')}
                />
              </div>
            </div>
          </div>

          <div className="card p-5 space-y-4">
            <h2 className="text-base font-semibold text-zinc-100">Templates</h2>

            <div>
              <label className="input-label" htmlFor="c-note">Connection note template</label>
              <textarea
                id="c-note"
                className="input-field min-h-[72px] resize-y"
                value={form.connection_note_template}
                onChange={setFormKey('connection_note_template')}
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
              <button
                type="button"
                onClick={addTemplate}
                className="mt-2 text-xs font-medium text-accent-400 transition hover:text-accent-300"
              >
                + Add another message
              </button>
            </div>
          </div>

          <div className="flex gap-4">
            <button
              type="submit"
              className="btn-primary flex-1"
              disabled={creating || steps.length === 0}
            >
              {creating && <Spinner />}
              {creating ? 'Saving...' : 'Create Campaign'}
            </button>
            <Link to="/app/campaigns" className="btn-secondary">
              Cancel
            </Link>
          </div>
        </div>

        {/* Right Column: Sequence Builder (Visual) */}
        <div className="grid grid-cols-1 gap-6 md:grid-cols-12 lg:col-span-8">
          {/* Sub-sidebar for Available Actions (Left part of creation grid) */}
          <div className="card p-5 space-y-4 md:col-span-4">
            <div>
              <h3 className="text-sm font-semibold text-zinc-100">Outreach Actions</h3>
              <p className="text-xs text-zinc-500 mt-1">
                Click an action to append it to your campaign drip sequence.
              </p>
            </div>

            <div className="space-y-3">
              {ACTION_TYPES.map((action) => (
                <button
                  key={action.type}
                  type="button"
                  onClick={() => appendStepFromSidebar(action.type)}
                  className={`flex w-full flex-col items-start gap-2 rounded-xl border p-3 text-left transition ${action.colorClasses}`}
                >
                  <div className="flex items-center gap-2">
                    {action.icon}
                    <span className="text-xs font-bold uppercase tracking-wider">{action.label}</span>
                  </div>
                  <p className="text-[11px] text-zinc-400 leading-normal">{action.desc}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Main workspace for Timeline Steps (Right part of creation grid) */}
          <div className="card p-6 md:col-span-8">
            <div className="flex items-center justify-between border-b border-surface-700 pb-4 mb-6">
              <div>
                <h3 className="text-sm font-semibold text-zinc-100">Drip Sequence Flow</h3>
                <p className="text-xs text-zinc-500 mt-0.5">
                  Set delay times and order your outreach events.
                </p>
              </div>
              <div className="flex items-center gap-2">
                {steps.length > 1 && (
                  <span className="text-xs rounded-full bg-amber-500/10 border border-amber-500/20 px-2.5 py-1 text-amber-300 font-mono flex items-center gap-1.5">
                    <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0" />
                    </svg>
                    Total: {formatTotalHours(totalDurationHours)}
                  </span>
                )}
                <span className="text-xs rounded-full bg-surface-800 border border-surface-700 px-2.5 py-1 text-zinc-400 font-mono">
                  {steps.length} {steps.length === 1 ? 'Step' : 'Steps'}
                </span>
              </div>
            </div>

            {steps.length === 0 ? (
              <div className="flex flex-col items-center justify-center border border-dashed border-surface-700 rounded-xl p-12 text-center bg-surface-900/40">
                <svg className="h-10 w-10 text-zinc-600 mb-3 animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v6m3-3H9m12 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
                </svg>
                <p className="text-sm font-medium text-zinc-300">No actions added</p>
                <p className="text-xs text-zinc-500 mt-1 max-w-xs">
                  Click an outreach action on the left library or click the plus button below to begin.
                </p>
                <button
                  type="button"
                  onClick={() => triggerPlusModal()}
                  className="btn-primary mt-4 py-1.5 px-3 text-xs"
                >
                  + Add First Action
                </button>
              </div>
            ) : (
              <div className="space-y-1 relative">
                {steps.map((step, index) => {
                  const details = ACTION_TYPES.find((a) => a.type === step.step_type) || ACTION_TYPES[0];
                  
                  return (
                    <div key={step.id} className="group relative">
                      {/* Interactive Time Delay Line between steps */}
                      {index > 0 && (
                        <div className="flex flex-col items-center my-1 relative">
                          <div className="h-6 w-0.5 bg-surface-700 group-hover:bg-accent-500/40 transition"></div>
                          
                          {/* Time difference bubble */}
                          <div className="relative group/bubble flex items-center justify-center my-1">
                            <div className="rounded-full bg-surface-800 hover:bg-surface-750 px-3.5 py-1 border border-surface-700 hover:border-accent-500/30 text-xs text-zinc-300 font-medium font-mono flex items-center gap-1.5 shadow-md transition cursor-pointer">
                              <svg className="h-3.5 w-3.5 text-accent-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0" />
                              </svg>
                              <span>
                                Wait {step.delay_val} {step.delay_val === 1 ? step.delay_unit.slice(0, -1) : step.delay_unit}
                              </span>
                            </div>
                            
                            {/* Insert Step button on hover of connector */}
                            <button
                              type="button"
                              onClick={() => triggerPlusModal(index)}
                              className="absolute -right-12 bg-accent-500/20 hover:bg-accent-500/30 border border-accent-500/30 hover:border-accent-500 text-accent-400 hover:text-accent-300 rounded-full p-1 opacity-0 group-hover/bubble:opacity-100 transition shadow"
                              title="Insert step here"
                            >
                              <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="3">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                              </svg>
                            </button>
                          </div>
                          
                          <div className="h-6 w-0.5 bg-surface-700 group-hover:bg-accent-500/40 transition"></div>
                        </div>
                      )}

                      {/* Step Card */}
                      <div className="card bg-surface-900 border-surface-750 p-4 relative hover:border-surface-600 transition shadow-inner">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          {/* Step Badge & Info */}
                          <div className="flex items-center gap-3">
                            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-surface-800 text-xs font-bold text-zinc-400 ring-1 ring-surface-700">
                              {index + 1}
                            </div>
                            <div>
                              <div className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${details.pillClasses}`}>
                                {details.icon}
                                {details.label}
                              </div>
                            </div>
                          </div>

                          {/* Quick Delay Settings inside card */}
                          <div className="flex items-center gap-2">
                            <div className="flex items-center gap-1.5 bg-surface-800 rounded-lg border border-surface-700 p-1">
                              <input
                                type="number"
                                min={0}
                                className="w-12 bg-transparent text-center text-xs font-mono text-zinc-100 outline-none"
                                value={step.delay_val}
                                onChange={(e) => updateStepDelay(step.id, e.target.value, step.delay_unit)}
                                title="Delay amount"
                              />
                              <select
                                className="bg-transparent text-xs text-zinc-400 font-medium outline-none pr-1 cursor-pointer"
                                value={step.delay_unit}
                                onChange={(e) => updateStepDelay(step.id, step.delay_val, e.target.value)}
                                title="Delay unit"
                              >
                                <option value="minutes" className="bg-surface-800 text-zinc-200">Min</option>
                                <option value="hours" className="bg-surface-800 text-zinc-200">Hr</option>
                                <option value="days" className="bg-surface-800 text-zinc-200">Day</option>
                              </select>
                            </div>

                            {/* Delete Button */}
                            <button
                              type="button"
                              onClick={() => removeStep(step.id)}
                              className="rounded-lg p-1.5 text-zinc-500 hover:bg-red-500/10 hover:text-red-400 transition"
                              title="Delete this step"
                            >
                              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
                                <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.34 9m-4.78 0L9 9m4.77-3.07V19c0 .11-.04.2-.11.28a.33.33 0 0 1-.28.11H9.62a.33.33 0 0 1-.28-.11.33.33 0 0 1-.11-.28V5.93M4.5 5.93h15M10.12 3h3.76" />
                              </svg>
                            </button>
                          </div>
                        </div>

                        {/* Extra step details */}
                        <div className="mt-3 flex items-center justify-between text-xs">
                          <p className="text-zinc-500 italic max-w-sm truncate">
                            {index === 0
                              ? "Executes immediately on campaign launch"
                              : `Runs ${step.delay_val} ${step.delay_unit} after previous step completes`}
                          </p>
                          <span className="text-[10px] font-mono text-zinc-600 shrink-0 ml-2" title="Fires at this time after campaign starts">
                            T+{formatTotalHours(cumulativeHours[index] || 0)}
                          </span>
                          
                          {/* Option to change Step Type inline */}
                          <select
                            className="bg-transparent text-[11px] text-zinc-500 hover:text-zinc-300 outline-none cursor-pointer border-none"
                            value={step.step_type}
                            onChange={(e) => updateStepType(step.id, e.target.value)}
                          >
                            {ACTION_TYPES.map(a => (
                              <option key={a.type} value={a.type} className="bg-surface-900 text-zinc-300">
                                Switch to: {a.label}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>
                    </div>
                  );
                })}

                {/* Big Plus Button at the bottom of the timeline */}
                <div className="flex flex-col items-center pt-4">
                  <div className="h-6 w-0.5 bg-surface-700"></div>
                  <button
                    type="button"
                    onClick={() => triggerPlusModal(null)}
                    className="group/btn flex h-10 w-10 items-center justify-center rounded-full border-2 border-dashed border-surface-600 bg-surface-850 hover:bg-surface-800 hover:border-accent-500/60 hover:text-accent-400 text-zinc-400 transition shadow-lg relative"
                    title="Add Outreach Step"
                  >
                    <svg className="h-5 w-5 transform group-hover/btn:rotate-90 transition duration-300" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2.5">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                    </svg>
                    
                    {/* Pulsing glow ring on hover */}
                    <div className="absolute -inset-1 rounded-full bg-accent-500/10 opacity-0 group-hover/btn:opacity-100 animate-ping -z-10 transition"></div>
                  </button>
                  <span className="text-[11px] text-zinc-500 mt-2 font-medium">Add Next Step</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </form>

      {/* Interactive Plus / Insert Step Modal */}
      {showPlusModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-fade-in">
          <div className="card w-full max-w-md p-6 bg-surface-900 border-surface-750 shadow-2xl animate-slide-up">
            <div className="flex items-center justify-between border-b border-surface-700 pb-3">
              <h3 className="text-base font-semibold text-zinc-100">
                {insertAtIndex === null ? 'Add Outreach Action' : `Insert Action before Step ${insertAtIndex + 1}`}
              </h3>
              <button
                type="button"
                onClick={() => setShowPlusModal(false)}
                className="text-zinc-500 hover:text-zinc-300 transition"
              >
                <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                  <path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" />
                </svg>
              </button>
            </div>

            <div className="mt-4 space-y-4">
              {/* Select Action */}
              <div>
                <label className="input-label">Select Action</label>
                <div className="grid grid-cols-2 gap-2">
                  {ACTION_TYPES.map((action) => (
                    <button
                      key={action.type}
                      type="button"
                      onClick={() => setNewStepAction(action.type)}
                      className={`flex items-center gap-2 rounded-lg border p-2.5 text-left transition ${
                        newStepAction === action.type
                          ? 'border-accent-500/50 bg-accent-500/10 text-accent-300 ring-1 ring-accent-500/20'
                          : 'border-surface-700 bg-surface-800 text-zinc-400 hover:bg-surface-750 hover:text-zinc-200'
                      }`}
                    >
                      {action.icon}
                      <span className="text-xs font-semibold">{action.label}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Set Delay */}
              <div>
                <label className="input-label">
                  {insertAtIndex === 0 ? 'Start Delay' : 'Time delay after previous step'}
                </label>
                <div className="flex gap-2">
                  <input
                    type="number"
                    min={0}
                    className="input-field flex-1"
                    value={newStepDelayVal}
                    onChange={(e) => setNewStepDelayVal(e.target.value)}
                  />
                  <select
                    className="input-field w-32 cursor-pointer"
                    value={newStepDelayUnit}
                    onChange={(e) => setNewStepDelayUnit(e.target.value)}
                  >
                    <option value="minutes">Minutes</option>
                    <option value="hours">Hours</option>
                    <option value="days">Days</option>
                  </select>
                </div>
                <p className="text-xs text-zinc-500 mt-1.5 italic">
                  Defines the exact elapsed time/difference before this action is executed.
                </p>
              </div>
            </div>

            <div className="mt-6 flex items-center justify-end gap-3 border-t border-surface-700 pt-4">
              <button
                type="button"
                onClick={() => setShowPlusModal(false)}
                className="btn-secondary px-4 py-1.5"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleAddStepConfirm}
                className="btn-primary px-4 py-1.5"
              >
                Add to Sequence
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
