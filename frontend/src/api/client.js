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

/**
 * Local-only expiry check for the boot-time session validation (page
 * refresh): decodes the stored access token and compares its ``exp`` claim
 * against the wall clock, with a leeway for clock skew.
 *
 * Returns ``false`` when the token carries no ``exp`` claim — in that case
 * the app cannot decide locally, so it lets the backend be the authority
 * (the normal 401 → refresh flow). Never throws.
 */
export function isAccessTokenExpired(leewaySeconds = 15) {
  const payload = decodeToken(getAccessToken());
  if (!payload || typeof payload.exp !== 'number') return false;
  const nowSeconds = Math.floor(Date.now() / 1000);
  return payload.exp <= nowSeconds + leewaySeconds;
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

/**
 * Broadcast when the session is definitively dead (unrefreshable 401). The
 * AuthProvider listens for it, clears its state, shows the login popup and
 * sends the user to /login — no jarring full-page reload.
 */
export const SESSION_EXPIRED_EVENT = 'auth:session-expired';

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

/**
 * Refresh the access token exactly once for all concurrent callers. Shared
 * by the 401 interceptor and the boot-time session check so a page refresh
 * never fires two refreshes at once.
 */
export function refreshSession() {
  if (!refreshPromise) {
    refreshPromise = doRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

/**
 * True only when the failure PROVES the session is dead: the refresh endpoint
 * rejected the refresh token (401/403) or there is no refresh token at all.
 * Network failures and 5xx are NOT proof — the session may be perfectly fine
 * and the backend simply unreachable, so the user must not be logged out.
 */
export function isDefinitiveAuthFailure(error) {
  const status = error?.response?.status;
  if (status === 401 || status === 403) return true;
  if (!status && error?.message === 'No refresh token') return true;
  return false;
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
        const newToken = await refreshSession();
        original.headers.Authorization = `Bearer ${newToken}`;
        return api(original);
      } catch (refreshError) {
        if (isDefinitiveAuthFailure(refreshError)) {
          // Log the exact URL + status so a deploy/env misconfiguration is
          // obvious: e.g. a 404 from Vercel/Railway's edge (backend not
          // deployed) or a 401 (JWT_SECRET changed since tokens were issued).
          const status = refreshError?.response?.status;
          const url = refreshError?.config?.url;
          console.warn(
            `[auth] token refresh failed${status ? ` (HTTP ${status})` : ''} — ` +
              `${url || 'unknown URL'}. Session cleared; redirecting to /login.`
          );
          clearSession();
          if (typeof window !== 'undefined') {
            // The AuthProvider shows the login popup and routes to /login.
            window.dispatchEvent(new window.Event(SESSION_EXPIRED_EVENT));
          }
        } else {
          console.warn(
            '[auth] token refresh unavailable (network/server issue) — keeping the session intact.'
          );
        }
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
