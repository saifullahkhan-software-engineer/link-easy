import { useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { socialSchedulerApi } from '../../api/socialScheduler';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../Spinner';

/**
 * In-upload editor: trim the uploaded clip and pick a thumbnail.
 *
 * Rendered only after a clip is on the server (an ``upload`` object exists).
 * Two server-side (ffmpeg) operations back it:
 *
 *   * **Trim** — re-encodes the stored clip to ``[start, end)`` *in place* via
 *     POST /uploads/{id}/trim. The publish pipeline reads that same file, so
 *     whatever is kept here is exactly what gets uploaded to the platforms.
 *   * **Thumbnail** — either a frame of the clip or an image from the user's
 *     PC, via POST /uploads/{id}/thumbnail. The chosen URL is stored with the
 *     post (``social_posts.thumbnail``) and exposed on the post record.
 *
 * ``duration_seconds`` is reported by the upload endpoint; if it is missing the
 * trim controls are hidden and only the thumbnail-from-PC option remains.
 */
function formatTime(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

function round1(value) {
  return Math.round((value || 0) * 10) / 10;
}

export default function VideoEditPanel({ upload, thumbnail, onThumbnailChange, onEdited }) {
  const uploadId = upload?.upload_id;
  const duration = Number(upload?.duration_seconds) || 0;

  // Trim range (seconds within the *current* clip). Reset whenever a
  // different clip is loaded — including after a trim, where the kept clip is
  // now full-length again and the scrubber must cover 0..new duration.
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(duration);
  const [trimming, setTrimming] = useState(false);
  useEffect(() => {
    setStart(0);
    setEnd(duration);
  }, [uploadId, duration]);

  // Thumbnail source + capture controls.
  const [tab, setTab] = useState('none');
  const [frameAt, setFrameAt] = useState(0);
  const [thumbBusy, setThumbBusy] = useState(false);
  const [thumbMeta, setThumbMeta] = useState(null); // { source, at_seconds } for captioning
  const imageInput = useRef(null);

  // A fresh clip starts with no thumbnail and a scrubber pointing at its
  // middle. Keyed on the clip id only (not on `thumbnail` or `duration`) so
  // the source tab never jumps after an image/frame is captured or a trim is
  // applied — the preview block below is what reflects the chosen cover.
  useEffect(() => {
    setFrameAt(round1(Math.max(0, Number(upload?.duration_seconds) || 0) / 2));
    setTab('none');
    setThumbMeta(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadId]);

  const canTrim = duration > 0;
  const clipLength = round1(Math.max(0, end - start));
  const trimmedSomething = start > 0.05 || end < duration - 0.05;

  const startSliderMax = useMemo(() => round1(Math.max(0, Math.min(duration, end - 0.1))), [duration, end]);
  const endSliderMin = useMemo(() => round1(Math.min(duration, start + 0.1)), [duration, start]);

  if (!upload) return null;

  const applyTrim = async () => {
    if (!canTrim) return;
    if (end - start < 0.1) return toast.error('The clip must keep at least 0.1 s.');
    setTrimming(true);
    try {
      const { data } = await socialSchedulerApi.trimVideo(uploadId, { start, end });
      onEdited(data); // same upload_id, new duration/size — refreshes the preview
      toast.success(trimmedSomething ? 'Trim applied' : 'Clip unchanged');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not trim the video'));
    } finally {
      setTrimming(false);
    }
  };

  const captureFrame = async () => {
    setThumbBusy(true);
    try {
      const at = Math.min(Math.max(Number(frameAt) || 0, 0), Math.max(duration - 0.05, 0));
      const { data } = await socialSchedulerApi.setThumbnailFrame(uploadId, at);
      setThumbMeta({ source: data.source, at_seconds: data.at_seconds });
      onThumbnailChange(data.thumbnail_url);
      toast.success('Thumbnail captured from the video');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not capture a frame'));
    } finally {
      setThumbBusy(false);
    }
  };

  const uploadImage = async (file) => {
    if (!file) return;
    setThumbBusy(true);
    try {
      const { data } = await socialSchedulerApi.setThumbnailImage(uploadId, file);
      setThumbMeta({ source: data.source, at_seconds: null });
      onThumbnailChange(data.thumbnail_url);
      toast.success('Thumbnail uploaded');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not upload that thumbnail'));
    } finally {
      setThumbBusy(false);
      if (imageInput.current) imageInput.current.value = '';
    }
  };

  const clearThumbnail = () => {
    onThumbnailChange('');
    setTab('none');
    setThumbMeta(null);
  };

  return (
    <div className="mt-5 rounded-xl border border-surface-700 bg-surface-900/40 p-4" data-testid="video-edit-panel">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-zinc-100">Edit &amp; thumbnail</h3>
          <p className="mt-0.5 text-xs text-zinc-500">
            Trim the clip and add a cover image before it is published. Changes are saved to this video.
          </p>
        </div>
        {!thumbnail && <span className="shrink-0 rounded-full border border-surface-600 px-2.5 py-1 text-[11px] text-zinc-400">optional</span>}
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {/* Preview + trim */}
        <div className="space-y-3">
          <video
            key={uploadId}
            src={upload.video_url}
            controls
            preload="metadata"
            className="max-h-64 w-full rounded-lg border border-surface-700 bg-black object-contain"
            data-testid="edit-video-preview"
          />

          <div>
            <div className="flex items-center justify-between">
              <p className="text-xs font-medium text-zinc-300">Trim</p>
              <p className="text-xs text-zinc-500" data-testid="trim-summary">
                {formatTime(start)} – {formatTime(end)} · {formatTime(clipLength)} kept
              </p>
            </div>

            {canTrim ? (
              <>
                <div className="mt-3 space-y-2">
                  <label className="flex items-center gap-3 text-xs text-zinc-400">
                    <span className="w-12 shrink-0">Start</span>
                    <input
                      type="range"
                      min={0}
                      max={startSliderMax}
                      step={0.1}
                      value={Math.min(start, startSliderMax)}
                      onChange={(e) => setStart(round1(Number(e.target.value)))}
                      data-testid="trim-start"
                      className="flex-1 accent-accent-500"
                    />
                    <span className="w-10 shrink-0 text-right tabular-nums text-zinc-300">{formatTime(start)}</span>
                  </label>
                  <label className="flex items-center gap-3 text-xs text-zinc-400">
                    <span className="w-12 shrink-0">End</span>
                    <input
                      type="range"
                      min={endSliderMin}
                      max={duration}
                      step={0.1}
                      value={Math.max(end, endSliderMin)}
                      onChange={(e) => setEnd(round1(Number(e.target.value)))}
                      data-testid="trim-end"
                      className="flex-1 accent-accent-500"
                    />
                    <span className="w-10 shrink-0 text-right tabular-nums text-zinc-300">{formatTime(end)}</span>
                  </label>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={applyTrim}
                    disabled={trimming}
                    aria-busy={trimming}
                    data-testid="apply-trim"
                    className="btn-secondary !px-3 !py-1.5 text-xs"
                  >
                    {trimming ? (
                      <span className="flex items-center gap-1.5">
                        <Spinner className="h-3 w-3" /> Trimming…
                      </span>
                    ) : trimmedSomething ? (
                      'Apply trim'
                    ) : (
                      'Keep full clip'
                    )}
                  </button>
                  {trimmedSomething && (
                    <button type="button" onClick={() => { setStart(0); setEnd(duration); }} className="text-xs text-zinc-500 hover:text-zinc-300">
                      Reset
                    </button>
                  )}
                </div>
              </>
            ) : (
              <p className="mt-2 text-xs text-zinc-500">
                This video&apos;s length could not be read on the server, so trimming is unavailable.
              </p>
            )}
          </div>
        </div>

        {/* Thumbnail */}
        <div className="space-y-3">
          <p className="text-xs font-medium text-zinc-300">Cover / thumbnail</p>
          <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Thumbnail source">
            {[
              { id: 'video', label: 'From video' },
              { id: 'upload', label: 'Upload image' },
            ].map((option) => (
              <button
                key={option.id}
                type="button"
                role="tab"
                aria-selected={tab === option.id}
                onClick={() => { setTab(option.id); if (option.id === 'upload') imageInput.current?.click(); }}
                data-testid={`thumb-tab-${option.id}`}
                className={`rounded-lg border px-3 py-1.5 text-xs transition ${
                  tab === option.id
                    ? 'border-accent-500/60 bg-accent-500/10 text-accent-200'
                    : 'border-surface-600 bg-surface-800 text-zinc-300 hover:border-surface-500'
                }`}
              >
                {option.label}
              </button>
            ))}
            <input
              ref={imageInput}
              type="file"
              accept="image/*"
              className="hidden"
              data-testid="thumb-image-input"
              onChange={(e) => uploadImage(e.target.files?.[0])}
            />
          </div>

          {tab === 'video' && (
            <div className="space-y-2">
              <label className="flex items-center gap-3 text-xs text-zinc-400">
                <span className="w-28 shrink-0">Frame at</span>
                <input
                  type="range"
                  min={0}
                  max={Math.max(duration, 0)}
                  step={0.1}
                  value={Math.min(frameAt, Math.max(duration, 0))}
                  onChange={(e) => setFrameAt(round1(Number(e.target.value)))}
                  data-testid="thumb-frame-time"
                  className="flex-1 accent-accent-500"
                />
                <span className="w-12 shrink-0 text-right tabular-nums text-zinc-300">{formatTime(frameAt)}</span>
              </label>
              <button
                type="button"
                onClick={captureFrame}
                disabled={thumbBusy || !canTrim}
                data-testid="capture-frame"
                className="btn-secondary !px-3 !py-1.5 text-xs disabled:opacity-50"
              >
                {thumbBusy ? (
                  <span className="flex items-center gap-1.5"><Spinner className="h-3 w-3" /> Capturing…</span>
                ) : (
                  'Use this frame as thumbnail'
                )}
              </button>
              {!canTrim && (
                <p className="text-xs text-zinc-500">Frame capture needs the server to read the video, which failed here.</p>
              )}
            </div>
          )}

          {tab === 'upload' && (
            <p className="text-xs text-zinc-400">
              Choose a cover image from your computer (PNG, JPG, WebP, …). It is resized and stored as the video&apos;s
              thumbnail.
            </p>
          )}

          {thumbnail ? (
            <div className="flex items-center gap-3 rounded-lg border border-surface-700 bg-surface-800/60 p-2">
              <img
                src={thumbnail}
                alt="Video thumbnail preview"
                data-testid="thumb-preview"
                className="h-20 w-14 rounded object-cover"
              />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium text-zinc-200">Thumbnail ready</p>
                <p className="truncate text-[11px] text-zinc-500">
                  {thumbMeta?.source === 'video_frame'
                    ? `frame @ ${formatTime(thumbMeta.at_seconds)}`
                    : thumbMeta?.source === 'upload'
                      ? 'uploaded image'
                      : 'cover image'}
                </p>
              </div>
              <button
                type="button"
                onClick={clearThumbnail}
                data-testid="clear-thumbnail"
                className="shrink-0 text-xs text-zinc-500 hover:text-rose-300"
              >
                Remove
              </button>
            </div>
          ) : (
            <p className="text-xs text-zinc-500" data-testid="no-thumbnail">
              No thumbnail set. Publish will proceed without a custom cover.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
