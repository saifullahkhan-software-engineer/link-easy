import axios from 'axios';

export const TOKEN_KEYS = {
  ACCESS: 'rp.access_token',
  REFRESH: 'rp.refresh_token',
  EMAIL: 'rp.user_email',
  NAME: 'rp.user_name',
};

export const getAccessToken = () => localStorage.getItem(TOKEN_KEYS.ACCESS);
export const getRefreshToken = () => localStorage.getItem(TOKEN_KEYS.REFRESH);
export const getUserEmail = () => localStorage.getItem(TOKEN_KEYS.EMAIL);
export const getUserName = () => localStorage.getItem(TOKEN_KEYS.NAME);

export function storeSession({ access_token, refresh_token, email, name }) {
  if (access_token) localStorage.setItem(TOKEN_KEYS.ACCESS, access_token);
  if (refresh_token) localStorage.setItem(TOKEN_KEYS.REFRESH, refresh_token);
  if (email) localStorage.setItem(TOKEN_KEYS.EMAIL, email);
  if (name) localStorage.setItem(TOKEN_KEYS.NAME, name);
}

export function clearSession() {
  Object.values(TOKEN_KEYS).forEach((k) => localStorage.removeItem(k));
}

/**
 * Axios instance pointed at the FastAPI backend (`/api/v1`).
 * - Attaches `Authorization: Bearer <access_token>` to every request.
 * - On 401, performs a single-flight token refresh (POST /auth/refresh)
 *   and replays the queued requests. If the refresh itself fails the
 *   session is cleared and the user is bounced to /login.
 */
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30_000,
});

api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// ---- single-flight refresh machinery ------------------------------------
let refreshPromise = null;

const NO_REFRESH_PATHS = ['/auth/login', '/auth/refresh', '/auth/register'];

async function doRefresh() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) throw new Error('No refresh token');
  // Plain axios call so we don't re-enter this interceptor.
  const { data } = await axios.post(
    `${api.defaults.baseURL}/auth/refresh`,
    { refresh_token: refreshToken },
    { timeout: 15_000 }
  );
  storeSession(data);
  return data.access_token;
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    const isApiError = Boolean(error.response);
    const isAuthPath = NO_REFRESH_PATHS.some((p) => original?.url?.includes(p));

    if (
      isApiError &&
      error.response.status === 401 &&
      original &&
      !original._retried &&
      !isAuthPath
    ) {
      original._retried = true;
      try {
        refreshPromise = refreshPromise || doRefresh();
        const newToken = await refreshPromise;
        refreshPromise = null;
        original.headers.Authorization = `Bearer ${newToken}`;
        return api(original);
      } catch (refreshError) {
        refreshPromise = null;
        clearSession();
        window.location.assign('/login');
        return Promise.reject(refreshError);
      }
    }
    return Promise.reject(error);
  }
);

/**
 * Pull a human-readable message out of a FastAPI error response.
 * Handles: string detail, object detail ({message, errors}), and
 * Pydantic's 422 validation array.
 */
export function getErrorMessage(error, fallback = 'Something went wrong') {
  const detail = error?.response?.data?.detail;
  if (!detail) return error?.message && !error?.response ? 'Network error — is the backend running?' : fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
  }
  if (typeof detail === 'object') return detail.message || fallback;
  return fallback;
}

export default api;
