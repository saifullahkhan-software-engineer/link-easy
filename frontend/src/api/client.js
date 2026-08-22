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

/**
 * Decode the payload of a JWT without verifying it.
 *
 * This is only ever used to decide what to *render*. The backend re-verifies
 * the signature on every request, so a tampered token buys nothing but a
 * broken-looking menu.
 */
export function decodeToken(token) {
  if (!token || typeof token !== 'string') return null;
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    // base64url -> base64, then decode as UTF-8 so non-ASCII names survive.
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
    const json = decodeURIComponent(
      atob(padded)
        .split('')
        .map((ch) => `%${`00${ch.charCodeAt(0).toString(16)}`.slice(-2)}`)
        .join('')
    );
    return JSON.parse(json);
  } catch {
    return null;
  }
}

/**
 * Roles for the signed-in user, read from the access token.
 *
 * Falls back to the legacy single ``role`` claim so tokens minted before
 * multi-role support still work, and defaults to ``customer`` so an
 * authenticated user is never locked out of the app dashboard.
 */
export function getUserRoles() {
  const payload = decodeToken(getAccessToken());
  if (!payload) return [];
  if (Array.isArray(payload.roles) && payload.roles.length) {
    return payload.roles.map((r) => String(r).toLowerCase());
  }
  if (payload.role) return [String(payload.role).toLowerCase()];
  return ['customer'];
}

export function clearSession() {
  try {
    Object.values(TOKEN_KEYS).forEach((k) => localStorage.removeItem(k));
    clearLegacyKeys();
  } catch {}
}

/**
 * Strip trailing slashes from a configured base URL so an env var like
 * ``https://api.example.com/api/v1/`` can never produce the double-slash
 * path ``/api/v1//auth/refresh`` (which 404s on FastAPI).
 */
export function normalizeBaseUrl(value) {
  return typeof value === 'string' ? value.replace(/\/+$/, '') : value;
}

export const API_BASE_URL = normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL || '/api/v1');

/**
 * Axios instance pointed at the FastAPI backend (`/api/v1`).
 * - Attaches `Authorization: Bearer <access_token>` to every request.
 * - On 401, performs a single-flight token refresh (POST /auth/refresh)
 *   and replays the queued requests. If the refresh itself fails the
 *   session is cleared and the user is bounced to /login.
 */
const api = axios.create({
  baseURL: API_BASE_URL,
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
        // Log the exact URL + status so a deploy/env misconfiguration is
        // obvious: e.g. a 404 from Vercel/Railway's edge (backend not
        // deployed) or a 401 (JWT_SECRET changed since tokens were issued).
        const status = refreshError?.response?.status;
        const url = refreshError?.config?.url;
        console.error(
          `[auth] token refresh failed${status ? ` (HTTP ${status})` : ''} — ` +
            `${url || 'unknown URL'}. Session cleared; redirected to /login.`
        );
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
  const responseData = error?.response?.data;
  const detail = responseData?.detail;
  if (!detail) {
    if (typeof responseData === 'string' && responseData.trim()) return responseData.trim();
    if (error?.message && !error?.response) return 'Network error — is the backend running?';
    const status = error?.response?.status;
    return status ? `${fallback || 'Request failed'} (HTTP ${status})` : fallback;
  }
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
  }
  if (typeof detail === 'object') return detail.message || fallback;
  return fallback;
}

export default api;
