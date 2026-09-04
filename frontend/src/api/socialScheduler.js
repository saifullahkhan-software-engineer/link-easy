import api from './client';

/**
 * Social post scheduler — YouTube Shorts / Instagram Reels / TikTok.
 *
 * Goes through the shared axios client so requests carry the bearer token,
 * hit the same origin / dev proxy as every other module (no hardcoded
 * localhost), and get the usual refresh-on-401 handling.
 *
 * Backend: api/v1/social_scheduler.py, prefix /api/v1/social-scheduler.
 */
const BASE = '/social-scheduler';

export const PLATFORMS = [
  { id: 'youtube', label: 'YouTube Shorts', short: 'YT' },
  { id: 'instagram', label: 'Instagram Reels', short: 'IG' },
  { id: 'tiktok', label: 'TikTok', short: 'TT' },
  { id: 'facebook', label: 'Facebook Reels', short: 'FB' },
];

export const PLATFORM_LABELS = Object.fromEntries(PLATFORMS.map((p) => [p.id, p.label]));

export const socialSchedulerApi = {
  // Posts
  listPosts: (params = {}) => api.get(`${BASE}/posts`, { params }),
  getPost: (postId) => api.get(`${BASE}/posts/${postId}`),
  createPost: (payload) => api.post(`${BASE}/posts`, payload),
  updatePost: (postId, payload) => api.patch(`${BASE}/posts/${postId}`, payload),
  cancelPost: (postId) => api.patch(`${BASE}/posts/${postId}`, { status: 'cancelled' }),
  // Re-queue a failed/cancelled post (optionally with a new time).
  requeuePost: (postId, scheduledAt) =>
    api.patch(`${BASE}/posts/${postId}`, {
      status: 'pending',
      ...(scheduledAt ? { scheduled_at: scheduledAt } : {}),
    }),
  deletePost: (postId) => api.delete(`${BASE}/posts/${postId}`),

  // Upload — multipart; a large video can take a while on a slow uplink.
  uploadVideo: (file, onProgress) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post(`${BASE}/upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 10 * 60_000,
      onUploadProgress: (event) => {
        if (onProgress && event.total) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      },
    });
  },

  // Edit an uploaded clip in place on the server (ffmpeg). `payload` is
  // { start, end } in seconds; the video file is re-encoded to that range and
  // the response is a fresh upload object (same upload_id, new duration/size)
  // the page can swap in for its preview. Returns the trimmed clip's metadata.
  trimVideo: (uploadId, payload) => api.post(`${BASE}/uploads/${uploadId}/trim`, payload, { timeout: 5 * 60_000 }),

  // Thumbnail for an upload. Two sources, both under the one endpoint:
  //   setThumbnailFrame  — extract a JPEG still of the clip at `at` seconds;
  //   setThumbnailImage  — store an image chosen from the user's PC.
  // Each returns { upload_id, thumbnail_url, source, at_seconds }.
  setThumbnailFrame: (uploadId, at) => {
    const formData = new FormData();
    formData.append('at', String(Number(at) || 0));
    return api.post(`${BASE}/uploads/${uploadId}/thumbnail`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60_000,
    });
  },
  setThumbnailImage: (uploadId, file) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post(`${BASE}/uploads/${uploadId}/thumbnail`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60_000,
    });
  },

  // AI copy extraction — split one large pasted message into the per-platform
  // title/description/hashtags the editor keeps. The backend calls Groq with
  // its own key (never the browser's), so this is a plain POST with the text;
  // the response's `platform_copy` drops straight into the form state.
  //
  // An LLM round trip beats the shared 30s default, so it gets its own
  // timeout; 400/413/502/503 all come back as normal axios errors for the
  // page to toast.
  parsePlatformCopy: (sourceText) =>
    api.post(`${BASE}/parse-copy`, { source_text: sourceText }, { timeout: 60_000 }),

  // Saved manual-share destinations (Facebook Groups). Meta removed the
  // Groups API in April 2024, so these are never posted by the backend — they
  // feed the upload page's picker and the post-publish checklist.
  listShareTargets: (platform = 'facebook') => api.get(`${BASE}/share-targets`, { params: { platform } }),
  createShareTarget: (payload) => api.post(`${BASE}/share-targets`, payload),
  deleteShareTarget: (targetId) => api.delete(`${BASE}/share-targets/${targetId}`),

  // Platform connections (OAuth)
  listPlatforms: () => api.get(`${BASE}/platforms`),
  // Playlists owned by the connected YouTube channel, for the upload editor's
  // "add this Short to…" picker. 409 = YouTube not connected, 502 = Google
  // refused the call (an expired token that could not be renewed, or a
  // connection made before the playlist permission was requested) — both are
  // shown inline; scheduling still works without them.
  listYouTubePlaylists: () => api.get(`${BASE}/platforms/youtube/playlists`),
  getAuthUrl: (platform) => api.get(`${BASE}/platforms/${platform}/auth-url`),
  disconnectPlatform: (platform) => api.delete(`${BASE}/platforms/${platform}`),

  // Platform app credentials — operator-set DB overrides of the environment
  // pair (admin-gated on the backend; secrets are write-only).
  listPlatformCredentials: () => api.get(`${BASE}/platforms/credentials`),
  savePlatformCredentials: (platform, payload) => api.put(`${BASE}/platforms/credentials/${platform}`, payload),
  deletePlatformCredentials: (platform) => api.delete(`${BASE}/platforms/credentials/${platform}`),

  // Aggregates
  getStats: () => api.get(`${BASE}/stats`),
  getCalendar: (month) => api.get(`${BASE}/calendar`, { params: month ? { month } : {} }),
};
