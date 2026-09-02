const SOCIAL_SCHEDULER_API = 'http://localhost:8000/api';

export const socialSchedulerApi = {
  // Posts
  getPosts: async () => {
    const response = await fetch(`${SOCIAL_SCHEDULER_API}/posts`);
    if (!response.ok) throw new Error('Failed to fetch posts');
    return response.json();
  },

  getPost: async (postId) => {
    const response = await fetch(`${SOCIAL_SCHEDULER_API}/posts/${postId}`);
    if (!response.ok) throw new Error('Failed to fetch post');
    return response.json();
  },

  createPost: async (postData) => {
    const response = await fetch(`${SOCIAL_SCHEDULER_API}/posts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(postData),
    });
    if (!response.ok) throw new Error('Failed to create post');
    return response.json();
  },

  updatePost: async (postId, postData) => {
    const response = await fetch(`${SOCIAL_SCHEDULER_API}/posts/${postId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(postData),
    });
    if (!response.ok) throw new Error('Failed to update post');
    return response.json();
  },

  deletePost: async (postId) => {
    const response = await fetch(`${SOCIAL_SCHEDULER_API}/posts/${postId}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete post');
    return response.json();
  },

  // Upload
  uploadVideo: async (file) => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${SOCIAL_SCHEDULER_API}/upload`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) throw new Error('Failed to upload video');
    return response.json();
  },

  // Platform connections
  getPlatformStatus: async () => {
    const response = await fetch(`${SOCIAL_SCHEDULER_API}/platforms/status`);
    if (!response.ok) throw new Error('Failed to fetch platform status');
    return response.json();
  },

  getPlatformConnections: async () => {
    const response = await fetch(`${SOCIAL_SCHEDULER_API}/platforms`);
    if (!response.ok) throw new Error('Failed to fetch platform connections');
    return response.json();
  },

  createPlatformConnection: async (connectionData) => {
    const response = await fetch(`${SOCIAL_SCHEDULER_API}/platforms`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(connectionData),
    });
    if (!response.ok) throw new Error('Failed to create platform connection');
    return response.json();
  },

  deletePlatformConnection: async (platform) => {
    const response = await fetch(`${SOCIAL_SCHEDULER_API}/platforms/${platform}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete platform connection');
    return response.json();
  },

  // Stats
  getStats: async () => {
    const response = await fetch(`${SOCIAL_SCHEDULER_API}/stats`);
    if (!response.ok) throw new Error('Failed to fetch stats');
    return response.json();
  },
};
