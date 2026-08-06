import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { feedScrollApi } from '../api/endpoints';
import { getUserEmail, getErrorMessage } from '../api/client';
import TagInput from '../components/feed/TagInput';
import { Spinner } from '../components/Spinner';

export default function FeedScrollEditPage() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const ownerEmail = getUserEmail();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [job, setJob] = useState(null);
  const [notFound, setNotFound] = useState(false);

  // Form state
  const [name, setName] = useState('');
  const [mode, setMode] = useState('job_search');

  // Job Search fields
  const [experienceMin, setExperienceMin] = useState('');
  const [experienceMax, setExperienceMax] = useState('');
  const [jobTitles, setJobTitles] = useState([]);
  const [skillSet, setSkillSet] = useState([]);

  // Keywords work in both modes: as an extra weighted signal for Job Search
  // and as the primary matching criteria for Post Search.
  const [keywords, setKeywords] = useState([]);

  // Pending text inputs (before tag conversion)
  const [pendingJobTitle, setPendingJobTitle] = useState('');
  const [pendingSkill, setPendingSkill] = useState('');
  const [pendingKeyword, setPendingKeyword] = useState('');

  // Scheduling
  const [intervalHours, setIntervalHours] = useState(1);

  useEffect(() => {
    loadJob();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  const loadJob = async () => {
    try {
      setLoading(true);
      const { data } = await feedScrollApi.getJob(jobId, ownerEmail);
      setJob(data);
      setName(data.name || '');
      setMode(data.mode || 'job_search');
      setExperienceMin(data.experience_min_years != null ? String(data.experience_min_years) : '');
      setExperienceMax(data.experience_max_years != null ? String(data.experience_max_years) : '');
      setJobTitles(data.job_titles || []);
      setSkillSet(data.skill_set || []);
      setKeywords(data.keywords || []);
      setIntervalHours(data.feed_interval_hours || 1);
    } catch (err) {
      setNotFound(true);
      toast.error(getErrorMessage(err, 'Failed to load feed scroll job'));
    } finally {
      setLoading(false);
    }
  };

  const parseTags = (text) => {
    if (!text) return [];
    return text
      .split(/[,;\n]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
  };

  const combineTags = (existingTags, pendingText) => {
    const extra = parseTags(pendingText);
    if (extra.length === 0) return existingTags;
    const combined = [...existingTags];
    for (const item of extra) {
      if (!combined.some((t) => t.toLowerCase() === item.toLowerCase())) {
        combined.push(item);
      }
    }
    return combined;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!name.trim()) {
      toast.error('Please enter a job name');
      return;
    }

    const finalJobTitles = combineTags(jobTitles, pendingJobTitle);
    const finalSkillSet = combineTags(skillSet, pendingSkill);
    const finalKeywords = combineTags(keywords, pendingKeyword);

    if (mode === 'job_search') {
      if (finalJobTitles.length === 0 && finalSkillSet.length === 0 && finalKeywords.length === 0) {
        toast.error('Please add at least one job title, skill, or keyword');
        return;
      }
      if (experienceMin !== '' && experienceMax !== '' && parseInt(experienceMin) > parseInt(experienceMax)) {
        toast.error('Minimum experience cannot exceed maximum experience');
        return;
      }
    } else {
      if (finalKeywords.length === 0) {
        toast.error('Please add at least one keyword');
        return;
      }
    }

    const payload = {
      name: name.trim(),
      feed_interval_hours: intervalHours,
    };

    if (mode === 'job_search') {
      payload.experience_min_years = experienceMin !== '' ? parseInt(experienceMin) : null;
      payload.experience_max_years = experienceMax !== '' ? parseInt(experienceMax) : null;
      payload.job_titles = finalJobTitles;
      payload.skill_set = finalSkillSet;
      payload.keywords = finalKeywords;
    } else {
      payload.keywords = finalKeywords;
    }

    try {
      setSaving(true);
      const { data } = await feedScrollApi.updateJob(jobId, ownerEmail, payload);
      if (data?.rescored_results != null) {
        toast.success(
          `Job updated — ${data.rescored_results} existing ${data.rescored_results === 1 ? 'post' : 'posts'} kept, ${data.removed_results || 0} removed under the new criteria`
        );
      } else {
        toast.success('Job updated');
      }
      navigate(`/app/feed-scroll/jobs/${jobId}`);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to update job'));
    } finally {
      setSaving(false);
    }
  };

  const intervalOptions = [1, 2, 4, 6, 8, 12, 24];

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (notFound || !job) {
    return (
      <div className="text-center text-zinc-400">
        <p>Job not found.</p>
        <Link to="/app/feed-scroll" className="mt-2 text-accent-400 hover:text-accent-300">
          ← Back to jobs
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl">
      <Link
        to={`/app/feed-scroll/jobs/${jobId}`}
        className="mb-2 inline-flex items-center gap-1 text-sm text-zinc-400 transition hover:text-zinc-200"
      >
        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
        </svg>
        Back to results
      </Link>

      <div className="mb-6">
        <h1 className="text-2xl font-bold text-zinc-100">Edit Feed Scroll Job</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Update keywords, experience, and job titles — the next scan picks up posts matching the new criteria
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6 rounded-xl border border-surface-700 bg-surface-800 p-6">
        {/* Note about re-scoring */}
        <div className="flex items-start gap-3 rounded-lg border border-accent-500/20 bg-accent-500/5 p-4">
          <svg className="h-5 w-5 shrink-0 text-accent-400 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 5.25h.008v.008H12v-.008Z" />
          </svg>
          <p className="text-sm leading-relaxed text-zinc-300">
            Changing <span className="font-semibold text-accent-300">keywords, experience, or job titles</span>{' '}
            immediately re-scores saved results: posts that no longer match are removed, and the next scan
            (or <span className="font-semibold text-accent-300">Scan Now</span>) picks up posts matching the new criteria.
          </p>
        </div>

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

        {/* Read-only account + mode */}
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">LinkedIn Account</label>
            <p className="rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-400">
              {job.account_email}
            </p>
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">Mode</label>
            <p className="rounded-lg border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-zinc-400">
              {job.mode === 'job_search' ? '🔍 Job Search' : '📝 Post Search'}
            </p>
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
              <p className="mt-1 text-xs text-zinc-500">
                Leave both empty to match any experience level.
              </p>
            </div>

            {/* Job Titles */}
            <div>
              <label className="mb-1.5 block text-sm font-medium text-zinc-300">
                Job Titles
              </label>
              <TagInput
                tags={jobTitles}
                onChange={setJobTitles}
                onPendingChange={setPendingJobTitle}
                placeholder="Type or paste titles (separated by commas or Enter)..."
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
                onPendingChange={setPendingSkill}
                placeholder="Type or paste skills (separated by commas or Enter)..."
              />
            </div>

            {/* Keywords */}
            <div>
              <label className="mb-1.5 flex items-center gap-2 text-sm font-medium text-zinc-300">
                Keywords / Extra Terms
                <span className="rounded bg-surface-700 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
                  Optional
                </span>
              </label>
              <TagInput
                tags={keywords}
                onChange={setKeywords}
                onPendingChange={setPendingKeyword}
                placeholder="e.g., remote, SaaS, hiring urgently (comma-separated or Enter)..."
              />
              <p className="mt-1 text-xs text-zinc-500">
                Add terms that make the job search more precise. Keyword matches add an extra relevance signal alongside titles, skills, and experience.
              </p>
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
                onPendingChange={setPendingKeyword}
                placeholder="Type or paste keywords (separated by commas or Enter)..."
              />
              <p className="mt-1 text-xs text-zinc-500">
                Add single words/phrases or paste comma-separated keywords. Posts matching these keywords will be scored and shown.
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
            onClick={() => navigate(`/app/feed-scroll/jobs/${jobId}`)}
            disabled={saving}
            className="rounded-lg border border-surface-700 bg-surface-700 px-4 py-2 text-sm font-medium text-zinc-300 transition hover:bg-surface-600 hover:text-zinc-100 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-400 disabled:opacity-50"
          >
            {saving ? (
              <>
                <Spinner />
                Saving...
              </>
            ) : (
              'Save Changes'
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
