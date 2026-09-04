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
import VideoEditPanel from '../../components/social/VideoEditPanel';

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
 * upload helper. This is deliberately small and predictable, and it is the
 * offline fallback for the AI extraction: POST /social-scheduler/parse-copy
 * (Groq, backend-only key) handles the awkward cases — captions that double as
 * hooks, Markdown headings, hashtags buried in the description — and writes
 * the same platform fields this function does.
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
  // Public URL of the chosen cover image (a frame of the clip or an uploaded
  // image), or '' for none. Persisted with the post as ``thumbnail``.
  const [thumbnail, setThumbnail] = useState('');
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [showPerPlatform, setShowPerPlatform] = useState(false);
  const [mode, setMode] = useState('schedule');
  const [sourceText, setSourceText] = useState('');
  const [aiExtracting, setAiExtracting] = useState(false);
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
    // YouTube playlists the Short is filed into once the upload succeeds.
    // Ignored (and sent empty) unless YouTube is one of the targets.
    youtube_playlist_ids: [],
    // Facebook Groups to share the Reel to by hand. Meta removed the Groups
    // API, so these are a post-publish checklist, never an upload target.
    facebook_groups: [],
  });
  // Saved Facebook Group destinations, same idle→loading→ready|error shape as
  // the playlist picker above.
  const [groupState, setGroupState] = useState({ status: 'idle', items: null, error: '' });
  const groupFetchStarted = useRef(false);
  const [groupReload, setGroupReload] = useState(0);
  const [newGroup, setNewGroup] = useState({ name: '', url: '' });
  const [savingGroup, setSavingGroup] = useState(false);
  // Playlist picker state: idle → loading → ready|error. `items: []` means the
  // channel really has no playlists, which is why "not fetched" needs its own
  // status rather than a null list.
  const [playlistState, setPlaylistState] = useState({ status: 'idle', items: null, error: '' });
  // Guards against a second fetch while one is in flight (React runs effects
  // twice in dev) — the effect's own deps change as soon as loading starts, so
  // a flag in the dep list would cancel the request it just made.
  const playlistFetchStarted = useRef(false);
  const [playlistReload, setPlaylistReload] = useState(0);

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

  const youtubeSelected = form.platforms.includes('youtube');
  const youtubeConnected = Boolean(connections?.find((c) => c.platform === 'youtube')?.connected);
  // Playlists are fetched only once YouTube is actually a target, so an
  // Instagram-only upload never pays for the round trip and never sees the
  // picker. One fetch per mount unless the retry button asks for another.
  useEffect(() => {
    if (!youtubeSelected || !youtubeConnected || playlistFetchStarted.current) return;
    playlistFetchStarted.current = true;
    setPlaylistState({ status: 'loading', items: null, error: '' });
    socialSchedulerApi
      .listYouTubePlaylists()
      .then(({ data }) => {
        setPlaylistState({ status: 'ready', items: data?.playlists || [], error: '' });
      })
      .catch((err) => {
        // Shown inline, never as a toast: publishing without playlists is a
        // perfectly normal outcome, so this must not look like a failed upload.
        setPlaylistState({
          status: 'error',
          items: null,
          error: getErrorMessage(err, 'Could not load your YouTube playlists'),
        });
      });
  }, [youtubeSelected, youtubeConnected, playlistReload]);

  const reloadPlaylists = () => {
    playlistFetchStarted.current = false;
    setPlaylistState({ status: 'idle', items: null, error: '' });
    setPlaylistReload((count) => count + 1);
  };

  const togglePlaylist = (playlistId) =>
    setForm((f) => ({
      ...f,
      youtube_playlist_ids: f.youtube_playlist_ids.includes(playlistId)
        ? f.youtube_playlist_ids.filter((id) => id !== playlistId)
        : [...f.youtube_playlist_ids, playlistId],
    }));

  // Saved groups load as soon as the page mounts: the picker is shown on every
  // upload, because sharing a published video to groups is a manual checklist
  // that works whether or not Facebook is one of the publish targets — it
  // needs no OAuth connection (nothing is ever posted to a group for the user).
  useEffect(() => {
    if (groupFetchStarted.current) return;
    groupFetchStarted.current = true;
    setGroupState({ status: 'loading', items: null, error: '' });
    socialSchedulerApi
      .listShareTargets('facebook')
      .then(({ data }) => {
        setGroupState({ status: 'ready', items: data || [], error: '' });
      })
      .catch((err) => {
        setGroupState({ status: 'error', items: null, error: getErrorMessage(err, 'Could not load your saved groups') });
      });
  }, [groupReload]);

  const toggleGroup = (target) =>
    setForm((f) => ({
      ...f,
      facebook_groups: f.facebook_groups.some((group) => group.url === target.url)
        ? f.facebook_groups.filter((group) => group.url !== target.url)
        : [...f.facebook_groups, { name: target.name, url: target.url }],
    }));

  const addGroup = async (event) => {
    event.preventDefault();
    const name = newGroup.name.trim();
    const url = newGroup.url.trim();
    if (!name) return toast.error('Give the group a name');
    // The backend enforces this too; checking here keeps the message friendly.
    if (!/^https?:\/\//i.test(url)) return toast.error('Paste the group link, starting with https://');

    setSavingGroup(true);
    try {
      const { data } = await socialSchedulerApi.createShareTarget({ name, url });
      setGroupState((state) =>
        state.status === 'ready' && !state.items.some((target) => target.id === data.id)
          ? { ...state, items: [...state.items, data] }
          : state,
      );
      setNewGroup({ name: '', url: '' });
      // A group added mid-upload is a group the user means to use.
      setForm((f) =>
        f.facebook_groups.some((group) => group.url === data.url)
          ? f
          : { ...f, facebook_groups: [...f.facebook_groups, { name: data.name, url: data.url }] },
      );
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not save that group'));
    } finally {
      setSavingGroup(false);
    }
    return undefined;
  };

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
    setThumbnail('');
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

  /**
   * Send the pasted message to the backend, which asks Groq to split it into
   * per-platform copy. The API key lives on the server; this page only sends
   * text and receives the same `platform_copy` shape it already edits, so the
   * manual fields below stay the source of truth and remain editable.
   */
  const extractWithAi = async () => {
    if (!sourceText.trim()) {
      toast.error('Paste your platform copy first');
      return;
    }
    setAiExtracting(true);
    try {
      const { data } = await socialSchedulerApi.parsePlatformCopy(sourceText);
      const copy = data?.platform_copy || {};
      const filled = PLATFORMS.filter(({ id }) =>
        ['title', 'description', 'hashtags'].some((field) => String(copy[id]?.[field] || '').trim()),
      );
      setForm((f) => ({
        ...f,
        title:
          f.title.trim() ||
          PLATFORMS.map(({ id }) => copy[id]?.title).find((value) => value?.trim()) ||
          '',
        platform_copy: {
          ...f.platform_copy,
          ...Object.fromEntries(
            PLATFORMS.map(({ id }) => [
              id,
              {
                title: copy[id]?.title || '',
                description: copy[id]?.description || '',
                hashtags: copy[id]?.hashtags || '',
              },
            ]),
          ),
        },
      }));
      setShowPerPlatform(true);
      if (filled.length) {
        toast.success(`Extracted copy for ${filled.map(({ label }) => label).join(', ')}`);
      } else {
        toast.error('No platform sections found in that message — check the headings, or fill the fields yourself.');
      }
    } catch (err) {
      toast.error(getErrorMessage(err, 'AI extraction failed'));
    } finally {
      setAiExtracting(false);
    }
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
        thumbnail,
        platforms: form.platforms,
        scheduled_at: when,
        publish_now: mode === 'direct',
        youtube_title: form.youtube_title,
        instagram_caption: form.instagram_caption,
        tiktok_caption: form.tiktok_caption,
        platform_copy: form.platform_copy,
        youtube_playlist_ids: youtubeSelected ? form.youtube_playlist_ids : [],
        // Groups are a manual share checklist, valid for any upload — even
        // when Facebook is not one of the publish targets.
        facebook_groups: form.facebook_groups,
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

          {upload && !uploading && (
            <VideoEditPanel
              upload={upload}
              thumbnail={thumbnail}
              onThumbnailChange={setThumbnail}
              onEdited={setUpload}
            />
          )}
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

        {/* YouTube playlists — only when a Short is going out */}
        {youtubeSelected && (
          <section className="card p-6" data-testid="youtube-playlists">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-zinc-100">Add to YouTube playlists</h2>
                <p className="mt-1 text-xs text-zinc-500">
                  Optional. The Short is filed into these playlists after it uploads — the upload itself never
                  waits on them.
                </p>
              </div>
              <span className="text-xs text-zinc-500">
                {form.youtube_playlist_ids.length} selected
              </span>
            </div>

            {!youtubeConnected && (
              <p className="mt-4 text-xs text-zinc-500">
                Connect YouTube in Settings to list the channel&apos;s playlists. The Short still publishes
                without one.
              </p>
            )}
            {youtubeConnected && playlistState.status === 'loading' && (
              <p className="mt-4 text-xs text-zinc-500" data-testid="playlists-loading">Loading playlists…</p>
            )}
            {youtubeConnected && playlistState.status === 'error' && (
              <div className="mt-4 flex items-start justify-between gap-3">
                <p className="text-xs text-amber-300">{playlistState.error}</p>
                <button
                  type="button"
                  onClick={reloadPlaylists}
                  data-testid="playlists-retry"
                  className="shrink-0 rounded-md border border-surface-600 px-3 py-1.5 text-xs text-zinc-200 hover:border-surface-500"
                >
                  Try again
                </button>
              </div>
            )}
            {youtubeConnected && playlistState.status === 'ready' && playlistState.items?.length === 0 && (
              <p className="mt-4 text-xs text-zinc-500">
                This channel has no playlists yet — create one on YouTube and reload.
              </p>
            )}
            {youtubeConnected && playlistState.status === 'ready' && playlistState.items?.length > 0 && (
              <div className="mt-4 max-h-64 space-y-2 overflow-y-auto pr-1">
                {playlistState.items.map((playlist) => {
                  const checked = form.youtube_playlist_ids.includes(playlist.id);
                  return (
                    <label
                      key={playlist.id}
                      className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 transition ${
                        checked
                          ? 'border-accent-500/60 bg-accent-500/10'
                          : 'border-surface-600 bg-surface-800 hover:border-surface-500'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => togglePlaylist(playlist.id)}
                        data-testid={`playlist-${playlist.id}`}
                        className="h-4 w-4 rounded border-surface-500 bg-surface-700 text-accent-500 focus:ring-accent-500/40"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-zinc-100">{playlist.title}</span>
                        <span className="block truncate text-xs text-zinc-500">
                          {playlist.item_count} {playlist.item_count === 1 ? 'video' : 'videos'}
                          {playlist.privacy && playlist.privacy !== 'public' ? ` · ${playlist.privacy}` : ''}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
            </section>
        )}

        {/* Facebook Groups — picked here, shared by hand after publishing */}
        <section className="card p-6" data-testid="facebook-groups">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-zinc-100">Share to Facebook groups</h2>
              <p className="mt-1 text-xs text-zinc-500">
                Optional, and available on every upload — no Facebook Page connection needed. Facebook closed its
                Groups API, so LinkEasy cannot post into a group for you: pick the groups here and the published post
                shows a checklist with each group one click away and the caption ready to copy.
              </p>
            </div>
            <span className="text-xs text-zinc-500">{form.facebook_groups.length} selected</span>
          </div>

            {groupState.status === 'loading' && (
              <p className="mt-4 text-xs text-zinc-500" data-testid="groups-loading">Loading your saved groups…</p>
            )}
            {groupState.status === 'error' && (
              <div className="mt-4 flex items-start justify-between gap-3">
                <p className="text-xs text-amber-300">{groupState.error}</p>
                <button
                  type="button"
                  onClick={() => {
                    groupFetchStarted.current = false;
                    setGroupState({ status: 'idle', items: null, error: '' });
                    setGroupReload((count) => count + 1);
                  }}
                  data-testid="groups-retry"
                  className="shrink-0 rounded-md border border-surface-600 px-3 py-1.5 text-xs text-zinc-200 hover:border-surface-500"
                >
                  Try again
                </button>
              </div>
            )}

            {groupState.status === 'ready' && groupState.items?.length > 0 && (
              <div className="mt-4 max-h-52 space-y-2 overflow-y-auto pr-1">
                {groupState.items.map((target) => {
                  const checked = form.facebook_groups.some((group) => group.url === target.url);
                  return (
                    <label
                      key={target.id}
                      className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 transition ${
                        checked
                          ? 'border-accent-500/60 bg-accent-500/10'
                          : 'border-surface-600 bg-surface-800 hover:border-surface-500'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleGroup(target)}
                        data-testid={`group-${target.id}`}
                        className="h-4 w-4 rounded border-surface-500 bg-surface-700 text-accent-500 focus:ring-accent-500/40"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-zinc-100">{target.name}</span>
                        <span className="block truncate text-xs text-zinc-500">{target.url}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
            {groupState.status === 'ready' && groupState.items?.length === 0 && (
              <p className="mt-4 text-xs text-zinc-500">
                No saved groups yet — add the first one below. It is stored for your next post too.
              </p>
            )}

            <form onSubmit={addGroup} className="mt-4 border-t border-surface-700 pt-4">
              <p className="mb-2 text-xs font-medium text-zinc-400">Add a group</p>
              <div className="flex flex-wrap gap-2">
                <input
                  value={newGroup.name}
                  onChange={(event) => setNewGroup((g) => ({ ...g, name: event.target.value }))}
                  placeholder="Group name"
                  aria-label="Group name"
                  data-testid="new-group-name"
                  maxLength={120}
                  className="min-w-[10rem] flex-1 rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-accent-500 focus:outline-none"
                />
                <input
                  value={newGroup.url}
                  onChange={(event) => setNewGroup((g) => ({ ...g, url: event.target.value }))}
                  placeholder="https://www.facebook.com/groups/…"
                  aria-label="Group link"
                  data-testid="new-group-url"
                  maxLength={500}
                  className="min-w-[14rem] flex-[2] rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-accent-500 focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={savingGroup}
                  data-testid="add-group"
                  className="rounded-lg border border-surface-600 px-4 py-2 text-sm text-zinc-200 transition hover:border-surface-500 disabled:opacity-50"
                >
                  {savingGroup ? 'Saving…' : 'Add'}
                </button>
              </div>
            </form>
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
                  Paste the complete YouTube, Instagram, TikTok or Facebook text generated by your workflow.
                  “Extract with AI” splits it into the per-platform fields below — headings, captions and
                  hashtags included. Prefer to stay offline? “Use text to fill fields” runs the same job in
                  your browser.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="btn-primary !px-3 !py-2 text-xs"
                  onClick={extractWithAi}
                  disabled={aiExtracting || !sourceText.trim()}
                  aria-busy={aiExtracting}
                  data-testid="ai-extract"
                >
                  {aiExtracting ? (
                    <span className="flex items-center gap-1.5">
                      <Spinner className="h-3 w-3" />
                      Extracting…
                    </span>
                  ) : (
                    'Extract with AI'
                  )}
                </button>
                <button
                  type="button"
                  className="btn-secondary !px-3 !py-2 text-xs"
                  onClick={fillFromPastedCopy}
                  disabled={aiExtracting || !sourceText.trim()}
                  data-testid="paste-fill"
                >
                  Use text to fill fields
                </button>
              </div>
            </div>
            <textarea
              className="input-field mt-3 min-h-[150px] bg-surface-900/70"
              value={sourceText}
              onChange={(e) => setSourceText(e.target.value)}
              placeholder={'Example:\n1. YouTube Shorts\nTitle: ...\nDescription: ...\n#Shorts #Python\n\n2. Instagram Reels\nHeadline (Caption Hook): ...'}
              aria-label="Paste full platform copy"
              data-testid="paste-source"
            />
            {aiExtracting && (
              <p className="mt-2 text-xs text-zinc-500">Reading your message with AI — this takes a few seconds.</p>
            )}
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
