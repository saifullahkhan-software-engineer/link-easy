import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { whatsappApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import TagInput from '../components/feed/TagInput';
import { Spinner } from '../components/Spinner';

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

export default function WhatsAppFilterCreatePage() {
  const navigate = useNavigate();
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [role, setRole] = useState('');
  const [jobTitle, setJobTitle] = useState('');
  const [keywords, setKeywords] = useState([]);
  const [pendingKeyword, setPendingKeyword] = useState('');
  const [experienceLevel, setExperienceLevel] = useState('');
  const [matchThreshold, setMatchThreshold] = useState(60);
  const [intervalHours, setIntervalHours] = useState(1);

  const handleSubmit = async (event) => {
    event.preventDefault();
    const finalKeywords = addPendingTags(keywords, pendingKeyword);

    if (!name.trim()) {
      toast.error('Please enter a filter name');
      return;
    }
    if (!role.trim() && !jobTitle.trim() && finalKeywords.length === 0) {
      toast.error('Add a role, job title, or keyword so the filter can match posts');
      return;
    }

    try {
      setSaving(true);
      const { data } = await whatsappApi.createFilterJob({
        name: name.trim(),
        role: role.trim() || null,
        job_title: jobTitle.trim() || null,
        keywords: finalKeywords.length ? finalKeywords : null,
        experience_level: experienceLevel || null,
        match_threshold: Number(matchThreshold),
        interval_hours: Number(intervalHours) || 1,
      });
      toast.success('WhatsApp filter created');
      navigate(`/app/whatsapp-scanner/jobs/${data.id}`);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to create WhatsApp filter'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-6 flex items-start gap-3">
        <Link to="/app/whatsapp-scanner" className="mt-1 text-zinc-500 transition hover:text-zinc-200" aria-label="Back to filters">
          ←
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-zinc-100">Create WhatsApp Filter</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Configure what to look for in WhatsApp job groups. You can select groups and start the filter from its detail page.
          </p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6 rounded-xl border border-surface-700 bg-surface-800 p-6">
        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">Filter Name</label>
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g., Senior Remote Engineering Jobs"
            autoFocus
            className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
          />
        </div>

        <div className="rounded-lg border border-accent-500/20 bg-accent-500/5 p-4 text-sm text-zinc-300">
          This filter will be created as a <span className="font-semibold text-accent-300">draft</span>. On the next page, select three monitored groups, choose a forwarding group, and press Start when you are ready.
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">Role</label>
            <input
              type="text"
              value={role}
              onChange={(event) => setRole(event.target.value)}
              placeholder="e.g., Software Engineer"
              className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">Job Title</label>
            <input
              type="text"
              value={jobTitle}
              onChange={(event) => setJobTitle(event.target.value)}
              placeholder="e.g., Backend Developer"
              className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
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
          {pendingKeyword && <p className="mt-1 text-xs text-zinc-500">Pending: {pendingKeyword}</p>}
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
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

        <div className="flex justify-end gap-3">
          <Link to="/app/whatsapp-scanner" className="rounded-lg border border-surface-700 px-4 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-700">
            Cancel
          </Link>
          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
          >
            {saving && <Spinner />}
            {saving ? 'Creating...' : 'Create Filter'}
          </button>
        </div>
      </form>
    </div>
  );
}
