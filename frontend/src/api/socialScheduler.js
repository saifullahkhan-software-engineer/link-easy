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

  // Platform connections (OAuth)
  listPlatforms: () => api.get(`${BASE}/platforms`),
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
