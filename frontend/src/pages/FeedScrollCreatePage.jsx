import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { feedScrollApi, linkedinApi } from '../api/endpoints';
import { getUserEmail, getErrorMessage } from '../api/client';
import TagInput from '../components/feed/TagInput';
import Spinner from '../components/Spinner';

export default function FeedScrollCreatePage() {
  const navigate = useNavigate();
  const ownerEmail = getUserEmail();

  const [loading, setLoading] = useState(false);
  const [accounts, setAccounts] = useState([]);
  const [accountsLoading, setAccountsLoading] = useState(true);

  // Form state
  const [name, setName] = useState('');
  const [accountEmail, setAccountEmail] = useState('');
  const [mode, setMode] = useState('job_search');

  // Job Search fields
  const [experienceMin, setExperienceMin] = useState('');
  const [experienceMax, setExperienceMax] = useState('');
  const [jobTitles, setJobTitles] = useState([]);
  const [skillSet, setSkillSet] = useState([]);

  // Post Search fields
  const [keywords, setKeywords] = useState([]);

  // Scheduling
  const [intervalHours, setIntervalHours] = useState(1);

  useEffect(() => {
    loadAccounts();
  }, []);

  const loadAccounts = async () => {
    try {
      const { data } = await linkedinApi.getAccount(ownerEmail);
      const accountList = Array.isArray(data) ? data : data ? [data] : [];
      setAccounts(accountList);
      if (accountList.length > 0 && !accountEmail) {
        setAccountEmail(accountList[0].linkedin_email);
      }
    } catch (err) {
      // No accounts yet
      setAccounts([]);
    } finally {
      setAccountsLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!name.trim()) {
      toast.error('Please enter a job name');
      return;
    }
    if (!accountEmail) {
      toast.error('Please select a LinkedIn account');
      return;
    }

    if (mode === 'job_search') {
      if (jobTitles.length === 0 && skillSet.length === 0) {
        toast.error('Please add at least one job title or skill');
        return;
      }
    } else {
      if (keywords.length === 0) {
        toast.error('Please add at least one keyword');
        return;
      }
    }

    const payload = {
      name: name.trim(),
      account_email: accountEmail,
      owner_email: ownerEmail,
      mode,
      feed_interval_hours: intervalHours,
      posts_per_scan: 10,
    };

    if (mode === 'job_search') {
      payload.experience_min_years = experienceMin ? parseInt(experienceMin) : null;
      payload.experience_max_years = experienceMax ? parseInt(experienceMax) : null;
      payload.job_titles = jobTitles;
      payload.skill_set = skillSet;
    } else {
      payload.keywords = keywords;
    }

    try {
      setLoading(true);
      await feedScrollApi.createJob(payload);
      toast.success('Feed scroll job created!');
      navigate('/app/feed-scroll');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to create job'));
    } finally {
      setLoading(false);
    }
  };

  const intervalOptions = [1, 2, 4, 6, 8, 12, 24];

  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-zinc-100">Create Feed Scroll Job</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Configure automated LinkedIn feed scanning to find relevant posts
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6 rounded-xl border border-surface-700 bg-surface-800 p-6">
        {/* Name */}
        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">Job Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g., Backend Job Hunt"
            className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
          />
        </div>

        {/* LinkedIn Account */}
        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">LinkedIn Account</label>
          {accountsLoading ? (
            <Spinner />
          ) : accounts.length === 0 ? (
            <p className="text-sm text-yellow-400">
              No LinkedIn accounts found. Please add one first.
            </p>
          ) : (
            <select
              value={accountEmail}
              onChange={(e) => setAccountEmail(e.target.value)}
              className="w-full rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
            >
              {accounts.map((acc) => (
                <option key={acc.linkedin_email} value={acc.linkedin_email}>
                  {acc.linkedin_email} {acc.label ? `(${acc.label})` : ''}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Mode */}
        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">Mode</label>
          <div className="flex gap-4">
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="radio"
                value="job_search"
                checked={mode === 'job_search'}
                onChange={(e) => setMode(e.target.value)}
                className="h-4 w-4 border-surface-600 bg-surface-900 text-accent-500 focus:ring-accent-500"
              />
              <span className="text-sm text-zinc-300">🔍 Job Search</span>
            </label>
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="radio"
                value="post_search"
                checked={mode === 'post_search'}
                onChange={(e) => setMode(e.target.value)}
                className="h-4 w-4 border-surface-600 bg-surface-900 text-accent-500 focus:ring-accent-500"
              />
              <span className="text-sm text-zinc-300">📝 Post Search</span>
            </label>
          </div>
        </div>

        {/* Job Search Configuration */}
        {mode === 'job_search' && (
          <div className="space-y-4 rounded-lg border border-surface-700 bg-surface-900 p-4">
            <p className="text-sm font-medium text-zinc-400">Job Search Configuration</p>

            {/* Experience Interval */}
            <div>
              <label className="mb-1.5 block text-sm font-medium text-zinc-300">
                Experience Interval (years)
              </label>
              <div className="flex items-center gap-3">
                <input
                  type="number"
                  min="0"
                  max="30"
                  value={experienceMin}
                  onChange={(e) => setExperienceMin(e.target.value)}
                  placeholder="Min"
                  className="w-24 rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
                />
                <span className="text-sm text-zinc-500">to</span>
                <input
                  type="number"
                  min="0"
                  max="30"
                  value={experienceMax}
                  onChange={(e) => setExperienceMax(e.target.value)}
                  placeholder="Max"
                  className="w-24 rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
                />
                <span className="text-sm text-zinc-500">years</span>
              </div>
            </div>

            {/* Job Titles */}
            <div>
              <label className="mb-1.5 block text-sm font-medium text-zinc-300">
                Job Titles
              </label>
              <TagInput
                tags={jobTitles}
                onChange={setJobTitles}
                placeholder="Type a title and press Enter..."
              />
            </div>

            {/* Skill Set */}
            <div>
              <label className="mb-1.5 block text-sm font-medium text-zinc-300">
                Skill Set
              </label>
              <TagInput
                tags={skillSet}
                onChange={setSkillSet}
                placeholder="Type a skill and press Enter..."
              />
            </div>
          </div>
        )}

        {/* Post Search Configuration */}
        {mode === 'post_search' && (
          <div className="space-y-4 rounded-lg border border-surface-700 bg-surface-900 p-4">
            <p className="text-sm font-medium text-zinc-400">Post Search Configuration</p>

            <div>
              <label className="mb-1.5 block text-sm font-medium text-zinc-300">
                Keywords / Topics
              </label>
              <TagInput
                tags={keywords}
                onChange={setKeywords}
                placeholder="Type a keyword and press Enter..."
              />
              <p className="mt-1 text-xs text-zinc-500">
                Posts matching these keywords will be scored and shown.
              </p>
            </div>
          </div>
        )}

        {/* Scheduling */}
        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">
            Feed Visit Interval
          </label>
          <select
            value={intervalHours}
            onChange={(e) => setIntervalHours(parseInt(e.target.value))}
            className="w-32 rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-100 focus:border-accent-500 focus:outline-none focus:ring-1 focus:ring-accent-500"
          >
            {intervalOptions.map((h) => (
              <option key={h} value={h}>
                {h} hour{h > 1 ? 's' : ''}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-zinc-500">
            How often to scan the LinkedIn feed for new posts.
          </p>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-end gap-3 border-t border-surface-700 pt-4">
          <button
            type="button"
            onClick={() => navigate('/app/feed-scroll')}
            className="rounded-lg border border-surface-700 bg-surface-700 px-4 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-600 hover:text-zinc-100"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
          >
            {loading ? (
              <>
                <Spinner />
                Creating...
              </>
            ) : (
              'Create Job'
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
