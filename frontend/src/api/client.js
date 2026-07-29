import axios from 'axios';

export const TOKEN_KEYS = {
  ACCESS: 'le.access_token',
  REFRESH: 'le.refresh_token',
  EMAIL: 'le.user_email',
  NAME: 'le.user_name',
  // backward compat: also clear old rp.* keys on logout
  LEGACY_ACCESS: 'rp.access_token',
  LEGACY_REFRESH: 'rp.refresh_token',
  LEGACY_EMAIL: 'rp.user_email',
  LEGACY_NAME: 'rp.user_name',
};

function clearLegacyKeys() {
  ['rp.access_token', 'rp.refresh_token', 'rp.user_email', 'rp.user_name'].forEach((k) => {
    try {
      localStorage.removeItem(k);
    } catch {}
  });
}

function getWithLegacy(newKey, legacyKey) {
  try {
    const v = localStorage.getItem(newKey);
    if (v) return v;
    const legacy = localStorage.getItem(legacyKey);
    if (legacy) {
      // migrate
      localStorage.setItem(newKey, legacy);
      localStorage.removeItem(legacyKey);
      return legacy;
    }
  } catch {}
  return null;
}

export const getAccessToken = () => getWithLegacy(TOKEN_KEYS.ACCESS, TOKEN_KEYS.LEGACY_ACCESS);
export const getRefreshToken = () => getWithLegacy(TOKEN_KEYS.REFRESH, TOKEN_KEYS.LEGACY_REFRESH);
export const getUserEmail = () => getWithLegacy(TOKEN_KEYS.EMAIL, TOKEN_KEYS.LEGACY_EMAIL);
export const getUserName = () => getWithLegacy(TOKEN_KEYS.NAME, TOKEN_KEYS.LEGACY_NAME);

export function storeSession({ access_token, refresh_token, email, name }) {
  try {
    if (access_token) localStorage.setItem(TOKEN_KEYS.ACCESS, access_token);
    if (refresh_token) localStorage.setItem(TOKEN_KEYS.REFRESH, refresh_token);
    if (email) localStorage.setItem(TOKEN_KEYS.EMAIL, email);
    if (name) localStorage.setItem(TOKEN_KEYS.NAME, name);
    clearLegacyKeys();
  } catch {}
}

export function clearSession() {
  try {
    Object.values(TOKEN_KEYS).forEach((k) => localStorage.removeItem(k));
    clearLegacyKeys();
  } catch {}
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
