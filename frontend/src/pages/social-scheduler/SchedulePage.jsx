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
  youtube_title: 100,
  instagram_caption: 2200,
  tiktok_caption: 2200,
};

function formatBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/**
 * Schedule a new post: upload one video, pick platforms, set a time and
 * (optionally) per-platform copy. The video is uploaded first and the post
 * references it by the server-issued upload_id.
 */
export default function SocialSchedulePage() {
  const navigate = useNavigate();
  const fileInput = useRef(null);

  const [connections, setConnections] = useState(null);
  const [file, setFile] = useState(null);
  const [upload, setUpload] = useState(null); // { upload_id, video_url, size_bytes }
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [showPerPlatform, setShowPerPlatform] = useState(false);
  const [form, setForm] = useState({
    title: '',
    caption: '',
    hashtags: '',
    platforms: [],
    scheduled_at: toLocalInputValue(null),
    youtube_title: '',
    instagram_caption: '',
    tiktok_caption: '',
  });

  useEffect(() => {
    socialSchedulerApi
      .listPlatforms()
      .then(({ data }) => {
        setConnections(data);
        // Pre-select whatever is connected so the common case is one click.
        setForm((f) => ({
          ...f,
          platforms: f.platforms.length ? f.platforms : data.filter((p) => p.connected).map((p) => p.platform),
        }));
      })
      .catch(() => setConnections([]));
  }, []);

  const update = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }));

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

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!upload) return toast.error('Upload a video first');
    if (!form.title.trim()) return toast.error('Give the post a title');
    if (form.platforms.length === 0) return toast.error('Pick at least one platform');
    const when = fromLocalInputValue(form.scheduled_at);
    if (!when) return toast.error('Choose when to publish');
    if (new Date(when).getTime() < Date.now() - 60_000) return toast.error('Pick a time in the future');

    setSubmitting(true);
    try {
      await socialSchedulerApi.createPost({
        title: form.title.trim(),
        caption: form.caption,
        hashtags: form.hashtags,
        upload_id: upload.upload_id,
        platforms: form.platforms,
        scheduled_at: when,
        youtube_title: form.youtube_title,
        instagram_caption: form.instagram_caption,
        tiktok_caption: form.tiktok_caption,
      });
      toast.success('Post scheduled');
      navigate('/app/social-scheduler/queue');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Failed to schedule the post'));
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
        title="Schedule a post"
        description="Upload one vertical video and publish it to every selected platform at the same moment."
      />

      <SchedulingDisabledNotice className="mb-6" />

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
                <p className="mt-1 text-xs text-zinc-500">
                  {formatBytes(upload.size_bytes)} · click to replace
                </p>
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
          <h2 className="text-base font-semibold text-zinc-100">Platforms</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
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
              {unconnectedSelected.length === 1 ? 'it' : 'them'} in Settings before the scheduled time or that
              publish will fail.
            </p>
          )}
        </section>

        {/* Copy */}
        <section className="card space-y-4 p-6">
          <h2 className="text-base font-semibold text-zinc-100">Details</h2>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300" htmlFor="sp-title">
              Title
            </label>
            <input
              id="sp-title"
              className="input-field"
              value={form.title}
              onChange={update('title')}
              maxLength={200}
              placeholder="Product launch teaser"
              required
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300" htmlFor="sp-caption">
              Caption
            </label>
            <textarea
              id="sp-caption"
              className="input-field min-h-[96px]"
              value={form.caption}
              onChange={update('caption')}
              maxLength={5000}
              placeholder="What is this video about?"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300" htmlFor="sp-hashtags">
              Hashtags
            </label>
            <input
              id="sp-hashtags"
              className="input-field"
              value={form.hashtags}
              onChange={update('hashtags')}
              maxLength={1000}
              placeholder="#launch #startup"
            />
            <p className="mt-1 text-xs text-zinc-500">Appended to the caption on every platform.</p>
          </div>

          <button
            type="button"
            onClick={() => setShowPerPlatform((v) => !v)}
            className="text-sm font-medium text-accent-400 hover:text-accent-300"
          >
            {showPerPlatform ? 'Hide' : 'Customise'} per-platform text
          </button>
          {showPerPlatform && (
            <div className="space-y-4 rounded-lg border border-surface-700 bg-surface-800/60 p-4">
              <Field
                id="sp-yt"
                label="YouTube title"
                hint={`Defaults to the title · ${form.youtube_title.length}/${LIMITS.youtube_title}`}
                value={form.youtube_title}
                onChange={update('youtube_title')}
                maxLength={LIMITS.youtube_title}
              />
              <Field
                id="sp-ig"
                label="Instagram caption"
                hint={`Defaults to caption + hashtags · ${form.instagram_caption.length}/${LIMITS.instagram_caption}`}
                value={form.instagram_caption}
                onChange={update('instagram_caption')}
                maxLength={LIMITS.instagram_caption}
                textarea
              />
              <Field
                id="sp-tt"
                label="TikTok caption"
                hint={`Defaults to caption + hashtags · ${form.tiktok_caption.length}/${LIMITS.tiktok_caption}`}
                value={form.tiktok_caption}
                onChange={update('tiktok_caption')}
                maxLength={LIMITS.tiktok_caption}
                textarea
              />
            </div>
          )}
        </section>

        {/* When */}
        <section className="card p-6">
          <h2 className="text-base font-semibold text-zinc-100">When</h2>
          <div className="mt-4 max-w-xs">
            <label className="mb-1.5 block text-sm font-medium text-zinc-300" htmlFor="sp-when">
              Publish at
            </label>
            <input
              id="sp-when"
              type="datetime-local"
              className="input-field"
              value={form.scheduled_at}
              onChange={update('scheduled_at')}
              required
            />
            <p className="mt-1 text-xs text-zinc-500">
              Your local time ({Intl.DateTimeFormat().resolvedOptions().timeZone}). The worker checks every minute.
            </p>
          </div>
        </section>

        <div className="flex items-center justify-end gap-3">
          <button type="button" className="btn-secondary" onClick={() => navigate('/app/social-scheduler')}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={submitting || uploading || !upload}>
            {submitting && <Spinner />}
            Schedule post
          </button>
        </div>
      </form>
    </div>
  );
}

function Field({ id, label, hint, value, onChange, maxLength, textarea = false }) {
  const Tag = textarea ? 'textarea' : 'input';
  return (
    <div>
      <label className="mb-1.5 block text-sm font-medium text-zinc-300" htmlFor={id}>
        {label}
      </label>
      <Tag
        id={id}
        className={`input-field ${textarea ? 'min-h-[80px]' : ''}`}
        value={value}
        onChange={onChange}
        maxLength={maxLength}
      />
      {hint && <p className="mt-1 text-xs text-zinc-500">{hint}</p>}
    </div>
  );
}
