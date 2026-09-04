import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { socialSchedulerApi, PLATFORMS } from '../../api/socialScheduler';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../../components/Spinner';
import SchedulingDisabledNotice from '../../components/SchedulingDisabledNotice';
import {
  PlatformIcon,
  SocialPageHeader,
  fromLocalInputValue,
  toLocalInputValue,
} from '../../components/social/SocialBits';

const ACCEPT = '.mp4,.mov,.m4v,.webm,video/mp4,video/quicktime,video/x-m4v,video/webm';
const LIMITS = {
  youtubeTitle: 100,
  copyDescription: 5000,
  copyHashtags: 1000,
};

const COPY_META = {
  youtube: {
    title: 'YouTube title',
    titleHint: 'The title shown on the Short',
    description: 'YouTube description',
    descriptionHint: 'Explain the Short and add any call to action',
  },
  instagram: {
    title: 'Headline / caption hook',
    titleHint: 'The first line people see in the Reel caption',
    description: 'Instagram description',
    descriptionHint: 'The body of the Reel caption',
  },
  tiktok: {
    title: 'Caption hook',
    titleHint: 'A short opening line for TikTok',
    description: 'TikTok description',
    descriptionHint: 'The body of the TikTok caption',
  },
  facebook: {
    title: 'Headline',
    titleHint: 'The opening line for the Facebook Reel',
    description: 'Facebook description',
    descriptionHint: 'The body of the Facebook Reel caption',
  },
};

const EMPTY_PLATFORM_COPY = Object.fromEntries(
  PLATFORMS.map(({ id }) => [id, { title: '', description: '', hashtags: '' }]),
);

function formatBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/**
 * Pull the most common labelled fields out of the copy format shown in the
 * upload helper. This is deliberately small and predictable; the optional
 * Groq integration can replace this function later without changing the
 * platform fields or the API contract.
 */
function extractPastedCopy(text) {
  const clean = String(text || '').trim();
  if (!clean) return {};
  const aliases = {
    youtube: /youtube\s+shorts?/i,
    instagram: /instagram\s+reels?/i,
    tiktok: /tiktok/i,
    facebook: /facebook\s+(?:reels?|page)/i,
  };
  const starts = Object.entries(aliases)
    .map(([platform, pattern]) => {
      const match = pattern.exec(clean);
      return match ? { platform, index: match.index } : null;
    })
    .filter(Boolean)
    .sort((a, b) => a.index - b.index);

  const output = {};
  starts.forEach(({ platform, index }, position) => {
    const end = position + 1 < starts.length ? starts[position + 1].index : clean.length;
    const section = clean.slice(index, end).replace(/\*\*/g, '').trim();
    const readLine = (labels) => {
      const expression = new RegExp(`(?:${labels})\\s*:\\s*([^\\n]+)`, 'i');
      return section.match(expression)?.[1]?.trim() || '';
    };
    const title = readLine('Title|Headline(?: \\(Caption Hook\\))?|Caption');
    const descriptionMatch = section.match(/Description\s*:\s*([\s\S]*?)(?=\n\s*(?:#|Hashtags?\s*:)|$)/i);
    const description = descriptionMatch?.[1]?.trim() || '';
    const hashtags = [...section.matchAll(/#[\p{L}\p{N}_-]+/gu)].map((match) => match[0]);
    output[platform] = {
      title,
      description: description.replace(/\s*\n\s*/g, '\n').trim(),
      hashtags: [...new Set(hashtags)].join(' '),
    };
  });
  return output;
}

/**
 * Upload one video, then either put it in the durable schedule queue or
 * dispatch it to the connected platforms immediately.
 */
export default function SocialSchedulePage() {
  const navigate = useNavigate();
  const fileInput = useRef(null);

  const [connections, setConnections] = useState(null);
  const [file, setFile] = useState(null);
  const [upload, setUpload] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [showPerPlatform, setShowPerPlatform] = useState(false);
  const [mode, setMode] = useState('schedule');
  const [sourceText, setSourceText] = useState('');
  const [form, setForm] = useState({
    title: '',
    caption: '',
    hashtags: '',
    platforms: [],
    scheduled_at: toLocalInputValue(null),
    youtube_title: '',
    instagram_caption: '',
    tiktok_caption: '',
    platform_copy: EMPTY_PLATFORM_COPY,
  });

  useEffect(() => {
    socialSchedulerApi
      .listPlatforms()
      .then(({ data }) => {
        setConnections(data);
        setForm((f) => ({
          ...f,
          platforms: f.platforms.length ? f.platforms : data.filter((p) => p.connected).map((p) => p.platform),
        }));
      })
      .catch(() => setConnections([]));
  }, []);

  const update = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }));

  const updatePlatformCopy = (platform, field) => (e) =>
    setForm((f) => ({
      ...f,
      platform_copy: {
        ...f.platform_copy,
        [platform]: { ...f.platform_copy[platform], [field]: e.target.value },
      },
    }));

  const togglePlatform = (id) =>
    setForm((f) => ({
      ...f,
      platforms: f.platforms.includes(id) ? f.platforms.filter((p) => p !== id) : [...f.platforms, id],
    }));

  const handleFile = async (picked) => {
    if (!picked) return;
    setFile(picked);
    setUpload(null);
    setUploadProgress(0);
    setUploading(true);
    try {
      const { data } = await socialSchedulerApi.uploadVideo(picked, setUploadProgress);
      setUpload(data);
      if (!form.title) {
        setForm((f) => ({ ...f, title: picked.name.replace(/\.[^.]+$/, '') }));
      }
      toast.success('Video uploaded');
    } catch (err) {
      setFile(null);
      toast.error(getErrorMessage(err, 'Upload failed'));
    } finally {
      setUploading(false);
    }
  };

  const onDrop = (e) => {
    e.preventDefault();
    handleFile(e.dataTransfer?.files?.[0]);
  };

  const fillFromPastedCopy = () => {
    const parsed = extractPastedCopy(sourceText);
    if (!Object.keys(parsed).length) {
      toast.error('No platform sections found. Use headings such as YouTube Shorts or Instagram Reels.');
      return;
    }
    setForm((f) => ({
      ...f,
      title: f.title || parsed.youtube?.title || parsed.instagram?.title || '',
      platform_copy: {
        ...f.platform_copy,
        ...Object.fromEntries(
          Object.entries(parsed).map(([platform, values]) => [
            platform,
            { ...f.platform_copy[platform], ...values },
          ]),
        ),
      },
    }));
    setShowPerPlatform(true);
    toast.success('Copy added to the platform fields');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!upload) return toast.error('Upload a video first');
    if (form.platforms.length === 0) return toast.error('Pick at least one platform');
    const fallbackTitle = form.platforms
      .map((platform) => form.platform_copy[platform]?.title)
      .find((value) => value?.trim()) || '';
    const postTitle = form.title.trim() || fallbackTitle.trim();
    if (!postTitle) return toast.error('Add a title in Details or in one platform section');

    let when = null;
    if (mode === 'schedule') {
      when = fromLocalInputValue(form.scheduled_at);
      if (!when) return toast.error('Choose when to publish');
      if (new Date(when).getTime() < Date.now() - 60_000) return toast.error('Pick a time in the future');
    } else {
      when = new Date().toISOString();
    }

    setSubmitting(true);
    try {
      await socialSchedulerApi.createPost({
        title: postTitle,
        caption: form.caption,
        hashtags: form.hashtags,
        upload_id: upload.upload_id,
        platforms: form.platforms,
        scheduled_at: when,
        publish_now: mode === 'direct',
        youtube_title: form.youtube_title,
        instagram_caption: form.instagram_caption,
        tiktok_caption: form.tiktok_caption,
        platform_copy: form.platform_copy,
      });
      toast.success(mode === 'direct' ? 'Video sent to the publish queue' : 'Post scheduled');
      navigate('/app/social-scheduler/queue');
    } catch (err) {
      toast.error(getErrorMessage(err, mode === 'direct' ? 'Failed to publish the video' : 'Failed to schedule the post'));
    } finally {
      setSubmitting(false);
    }
  };

  const connectionFor = (id) => connections?.find((c) => c.platform === id);
  const unconnectedSelected = form.platforms.filter((id) => connections && !connectionFor(id)?.connected);

  return (
    <div className="mx-auto max-w-4xl">
      <SocialPageHeader
        current="/app/social-scheduler/schedule"
        title="Upload a video"
        description="Upload one vertical video, customise the copy for each connected platform, then publish now or schedule it."
      />

      {mode === 'schedule' && <SchedulingDisabledNotice className="mb-6" />}

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Video */}
        <section className="card p-6">
          <h2 className="text-base font-semibold text-zinc-100">Video</h2>
          <p className="mt-1 text-xs text-zinc-500">MP4 or MOV, 9:16 vertical, ≤ 60 s works everywhere.</p>

          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop}
            onClick={() => !uploading && fileInput.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && fileInput.current?.click()}
            className={`mt-4 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition ${
              upload
                ? 'border-emerald-500/40 bg-emerald-500/5'
                : 'border-surface-600 bg-surface-800/60 hover:border-accent-500/50 hover:bg-surface-800'
            }`}
          >
            <input
              ref={fileInput}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => handleFile(e.target.files?.[0])}
              data-testid="video-input"
            />
            {uploading ? (
              <>
                <Spinner className="h-6 w-6 text-accent-400" />
                <p className="mt-3 text-sm text-zinc-300">Uploading {file?.name}… {uploadProgress}%</p>
                <div className="mt-3 h-1.5 w-64 overflow-hidden rounded-full bg-surface-700">
                  <div className="h-full bg-accent-500 transition-all" style={{ width: `${uploadProgress}%` }} />
                </div>
              </>
            ) : upload ? (
              <>
                <p className="text-sm font-medium text-emerald-300">✓ {file?.name}</p>
                <p className="mt-1 text-xs text-zinc-500">{formatBytes(upload.size_bytes)} · click to replace</p>
              </>
            ) : (
              <>
                <svg className="h-8 w-8 text-zinc-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0 3 3m-3-3-3 3M6.75 19.5a4.5 4.5 0 0 1-1.41-8.775 5.25 5.25 0 0 1 10.233-2.33 3 3 0 0 1 3.758 3.848A3.752 3.752 0 0 1 18 19.5H6.75Z" />
                </svg>
                <p className="mt-3 text-sm font-medium text-zinc-200">Drop a video here or click to browse</p>
                <p className="mt-1 text-xs text-zinc-500">MP4 · MOV · M4V · WEBM</p>
              </>
            )}
          </div>
        </section>

        {/* Platforms */}
        <section className="card p-6">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-zinc-100">Platforms</h2>
              <p className="mt-1 text-xs text-zinc-500">Choose the connected accounts that should receive this video.</p>
            </div>
            <span className="text-xs text-zinc-500">{form.platforms.length} selected</span>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {PLATFORMS.map((p) => {
              const conn = connectionFor(p.id);
              const selected = form.platforms.includes(p.id);
              return (
                <button
                  type="button"
                  key={p.id}
                  onClick={() => togglePlatform(p.id)}
                  aria-pressed={selected}
                  className={`flex items-center gap-3 rounded-lg border px-4 py-3 text-left transition ${
                    selected
                      ? 'border-accent-500/60 bg-accent-500/10 ring-1 ring-inset ring-accent-500/30'
                      : 'border-surface-600 bg-surface-800 hover:border-surface-500'
                  }`}
                >
                  <PlatformIcon platform={p.id} className={`h-8 w-8 rounded-lg ${selected ? 'bg-accent-500/20 text-accent-300' : 'bg-surface-700 text-zinc-400'}`} />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-zinc-100">{p.label}</span>
                    <span className={`block text-xs ${conn?.connected ? 'text-emerald-400' : 'text-zinc-500'}`}>
                      {connections === null ? '…' : conn?.connected ? conn.account_name || 'Connected' : 'Not connected'}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
          {unconnectedSelected.length > 0 && (
            <p className="mt-3 text-xs text-amber-300">
              {unconnectedSelected.map((id) => PLATFORMS.find((p) => p.id === id)?.label).join(', ')}{' '}
              {unconnectedSelected.length === 1 ? 'is' : 'are'} not connected — connect{' '}
              {unconnectedSelected.length === 1 ? 'it' : 'them'} in Settings before publishing or that platform will fail.
            </p>
          )}
        </section>

        {/* Copy */}
        <section className="card space-y-4 p-6">
          <div>
            <h2 className="text-base font-semibold text-zinc-100">Details</h2>
            <p className="mt-1 text-xs text-zinc-500">These fields are the shared fallback. Platform-specific copy below takes priority.</p>
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300" htmlFor="sp-title">Internal post title</label>
            <input id="sp-title" className="input-field" value={form.title} onChange={update('title')} maxLength={200} placeholder="Day 17 — Dictionaries in Python" />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300" htmlFor="sp-caption">Shared description</label>
            <textarea id="sp-caption" className="input-field min-h-[96px]" value={form.caption} onChange={update('caption')} maxLength={5000} placeholder="Used when a platform does not have custom copy" />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300" htmlFor="sp-hashtags">Shared hashtags</label>
            <input id="sp-hashtags" className="input-field" value={form.hashtags} onChange={update('hashtags')} maxLength={1000} placeholder="#Shorts #Python #LearnToCode" />
            <p className="mt-1 text-xs text-zinc-500">Used as a fallback when a platform does not have custom hashtags.</p>
          </div>

          <div className="rounded-xl border border-accent-500/20 bg-accent-500/5 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-zinc-100">Paste your platform copy</h3>
                <p className="mt-1 max-w-2xl text-xs leading-5 text-zinc-400">
                  Paste the complete YouTube, Instagram, TikTok or Facebook text generated by your workflow. The helper recognises the labelled format in your examples. Groq can later populate these same fields through this editor.
                </p>
              </div>
              <button type="button" className="btn-secondary !px-3 !py-2 text-xs" onClick={fillFromPastedCopy} disabled={!sourceText.trim()}>
                Use text to fill fields
              </button>
            </div>
            <textarea
              className="input-field mt-3 min-h-[150px] bg-surface-900/70"
              value={sourceText}
              onChange={(e) => setSourceText(e.target.value)}
              placeholder={'Example:\n1. YouTube Shorts\nTitle: ...\nDescription: ...\n#Shorts #Python\n\n2. Instagram Reels\nHeadline (Caption Hook): ...'}
              aria-label="Paste full platform copy"
            />
          </div>

          <button
            type="button"
            onClick={() => setShowPerPlatform((v) => !v)}
            className="flex w-full items-center justify-between rounded-lg border border-surface-700 bg-surface-800/50 px-4 py-3 text-left text-sm font-medium text-accent-400 hover:text-accent-300"
            aria-expanded={showPerPlatform}
          >
            <span>Customise per-platform text <span className="ml-1 text-xs font-normal text-zinc-500">({form.platforms.length || 0} selected)</span></span>
            <span aria-hidden="true">{showPerPlatform ? '−' : '+'}</span>
          </button>
          {showPerPlatform && (
            <div className="space-y-4 rounded-lg border border-surface-700 bg-surface-800/60 p-4">
              {form.platforms.length === 0 ? (
                <p className="text-sm text-zinc-500">Select at least one platform above to customise its copy.</p>
              ) : (
                form.platforms.map((platform) => {
                  const meta = COPY_META[platform];
                  const platformInfo = PLATFORMS.find((p) => p.id === platform);
                  const values = form.platform_copy[platform] || EMPTY_PLATFORM_COPY[platform];
                  return (
                    <div key={platform} className="rounded-xl border border-surface-700 bg-surface-900/60 p-4">
                      <div className="mb-3 flex items-center gap-2">
                        <PlatformIcon platform={platform} className="h-6 w-6 text-zinc-300" />
                        <div>
                          <h3 className="text-sm font-semibold text-zinc-100">{platformInfo?.label || platform}</h3>
                          <p className="text-xs text-zinc-500">Title, description and hashtags for this platform</p>
                        </div>
                      </div>
                      <div className="space-y-3">
                        <CopyField
                          id={`copy-${platform}-title`}
                          label={meta.title}
                          hint={`${meta.titleHint}${platform === 'youtube' ? ` · ${values.title.length}/${LIMITS.youtubeTitle}` : ''}`}
                          value={values.title}
                          onChange={updatePlatformCopy(platform, 'title')}
                          maxLength={platform === 'youtube' ? LIMITS.youtubeTitle : 2200}
                        />
                        <CopyField
                          id={`copy-${platform}-description`}
                          label={meta.description}
                          hint={meta.descriptionHint}
                          value={values.description}
                          onChange={updatePlatformCopy(platform, 'description')}
                          maxLength={LIMITS.copyDescription}
                          textarea
                        />
                        <CopyField
                          id={`copy-${platform}-hashtags`}
                          label="Hashtags"
                          hint={`Keep hashtags separate so they can be changed per platform · ${values.hashtags.length}/${LIMITS.copyHashtags}`}
                          value={values.hashtags}
                          onChange={updatePlatformCopy(platform, 'hashtags')}
                          maxLength={LIMITS.copyHashtags}
                        />
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          )}
        </section>

        {/* Publish mode */}
        <section className="card p-6">
          <h2 className="text-base font-semibold text-zinc-100">Publish</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-2" role="group" aria-label="Publish mode">
            <button
              type="button"
              onClick={() => setMode('schedule')}
              aria-pressed={mode === 'schedule'}
              className={`rounded-xl border p-4 text-left transition ${mode === 'schedule' ? 'border-accent-500/60 bg-accent-500/10 ring-1 ring-inset ring-accent-500/30' : 'border-surface-600 bg-surface-800 hover:border-surface-500'}`}
            >
              <span className="block text-sm font-semibold text-zinc-100">Schedule</span>
              <span className="mt-1 block text-xs leading-5 text-zinc-500">Choose a future date and time. The worker publishes it automatically.</span>
            </button>
            <button
              type="button"
              onClick={() => setMode('direct')}
              aria-pressed={mode === 'direct'}
              className={`rounded-xl border p-4 text-left transition ${mode === 'direct' ? 'border-accent-500/60 bg-accent-500/10 ring-1 ring-inset ring-accent-500/30' : 'border-surface-600 bg-surface-800 hover:border-surface-500'}`}
            >
              <span className="block text-sm font-semibold text-zinc-100">Direct upload</span>
              <span className="mt-1 block text-xs leading-5 text-zinc-500">Send the video to the publish queue immediately after upload.</span>
            </button>
          </div>
          {mode === 'schedule' && (
            <div className="mt-4 max-w-xs">
              <label className="mb-1.5 block text-sm font-medium text-zinc-300" htmlFor="sp-when">Publish at</label>
              <input id="sp-when" type="datetime-local" className="input-field" value={form.scheduled_at} onChange={update('scheduled_at')} required />
              <p className="mt-1 text-xs text-zinc-500">Your local time ({Intl.DateTimeFormat().resolvedOptions().timeZone}). The worker checks every minute.</p>
            </div>
          )}
        </section>

        <div className="flex items-center justify-end gap-3">
          <button type="button" className="btn-secondary" onClick={() => navigate('/app/social-scheduler')}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={submitting || uploading || !upload}>
            {submitting && <Spinner />}
            {mode === 'direct' ? 'Upload & publish now' : 'Schedule post'}
          </button>
        </div>
      </form>
    </div>
  );
}

function CopyField({ id, label, hint, value, onChange, maxLength, textarea = false }) {
  const Tag = textarea ? 'textarea' : 'input';
  return (
    <div>
      <label className="mb-1.5 block text-sm font-medium text-zinc-300" htmlFor={id}>{label}</label>
      <Tag id={id} className={`input-field ${textarea ? 'min-h-[100px]' : ''}`} value={value} onChange={onChange} maxLength={maxLength} />
      {hint && <p className="mt-1 text-xs text-zinc-500">{hint}</p>}
    </div>
  );
}
