import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  clearSession,
  getAccessToken,
  getSessionExpiresAt,
  getUserEmail,
  getUserName,
  getUserRoles,
  isAccessTokenExpired,
  isDefinitiveAuthFailure,
  refreshSession,
  SESSION_EXPIRED_EVENT,
  storeSession,
} from '../api/client';
import { authApi } from '../api/endpoints';
import SessionExpiredDialog from '../components/SessionExpiredDialog';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const navigate = useNavigate();
  const [token, setToken] = useState(() => getAccessToken());
  const [email, setEmail] = useState(() => getUserEmail());
  const [name, setName] = useState(() => getUserName());
  // Roles come from the access token, so they refresh whenever the token does
  // and cannot drift out of sync with a separately stored copy.
  const [roles, setRoles] = useState(() => getUserRoles());
  const [sessionExpired, setSessionExpired] = useState(false);
  // True only while the boot-time session validation below is refreshing an
  // expired access token. Protected routes render a neutral loading screen in
  // the meantime — a page refresh never flashes page content or fires a storm
  // of doomed API calls for a session that turns out to be dead.
  const [isCheckingSession, setIsCheckingSession] = useState(() => {
    const t = getAccessToken();
    return Boolean(t && isAccessTokenExpired());
  });

  /**
   * The single "session is invalid" path, from wherever it was discovered
   * (boot check or the axios interceptor): clear the stored session, show
   * the login popup and take the user to the login page.
   */
  const handleSessionExpired = useCallback(() => {
    clearSession();
    setToken(null);
    setEmail(null);
    setName(null);
    setRoles([]);
    setSessionExpired(true);
    navigate('/login', { replace: true });
  }, [navigate]);

  const dismissSessionExpired = useCallback(() => setSessionExpired(false), []);

  // Enforce the absolute session deadline in the browser too. Without this
  // timer, a user who leaves the app open with no API traffic could still see
  // authenticated UI after the two-hour server deadline. The server remains
  // authoritative; this is the immediate client-side logout path.
  useEffect(() => {
    const expiresAt = getSessionExpiresAt(token);
    if (expiresAt === null) return undefined;

    const remaining = expiresAt - Date.now();
    if (remaining <= 0) {
      handleSessionExpired();
      return undefined;
    }

    const timeoutId = window.setTimeout(handleSessionExpired, remaining);
    return () => window.clearTimeout(timeoutId);
  }, [token, handleSessionExpired]);

  // Boot-time session validation — this runs on every page load / refresh:
  //  • access token still valid          → nothing to do; the page simply
  //                                        renders, no errors on refresh
  //  • expired, but the refresh succeeds → silently renewed; the user stays
  //                                        exactly where they were
  //  • expired and cannot be refreshed   → the session is invalid: clear it,
  //                                        show the login popup, go to /login
  useEffect(() => {
    let cancelled = false;

    async function validateSession() {
      if (!getAccessToken() || !isAccessTokenExpired()) return;
      try {
        // Throws "No refresh token" when none is stored.
        await refreshSession();
        if (cancelled) return;
        // Pick up the refreshed token so roles/UI stay in sync immediately.
        setToken(getAccessToken());
        setRoles(getUserRoles());
      } catch (err) {
        if (cancelled) return;
        if (isDefinitiveAuthFailure(err)) {
          handleSessionExpired();
        } else {
          // Backend unreachable / 5xx: the session was NOT proven invalid —
          // keep it and let pages surface their normal API errors instead of
          // wrongly bouncing the user to the login screen.
          console.warn(
            '[auth] session check could not reach the server — keeping the session intact.'
          );
        }
      } finally {
        if (!cancelled) setIsCheckingSession(false);
      }
    }

    validateSession();
    return () => {
      cancelled = true;
    };
  }, [handleSessionExpired]);

  // A mid-session 401 that cannot be refreshed is signalled by the axios
  // interceptor through this event → same popup + /login flow.
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const listener = () => handleSessionExpired();
    window.addEventListener(SESSION_EXPIRED_EVENT, listener);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, listener);
  }, [handleSessionExpired]);

  const login = useCallback(async (loginEmail, password) => {
    const { data } = await authApi.login(loginEmail, password);
    storeSession({ ...data, email: loginEmail });
    setToken(data.access_token);
    setEmail(loginEmail);
    setName(getUserName());
    setRoles(getUserRoles());
    // Logging in always resolves any pending "session expired" notice.
    setSessionExpired(false);
    return data;
  }, []);

  const completeSignup = useCallback((signupEmail, signUpName) => {
    storeSession({ email: signupEmail, name: signUpName });
    setEmail(signupEmail);
    setName(signUpName);
  }, []);

  const setNameOnly = useCallback((n) => {
    storeSession({ name: n });
    setName(n);
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setToken(null);
    setEmail(null);
    setName(null);
    setRoles([]);
  }, []);

  // Re-read roles from a token refreshed by the axios interceptor.
  const syncRoles = useCallback(() => {
    setToken(getAccessToken());
    setRoles(getUserRoles());
  }, []);

  const value = useMemo(
    () => ({
      isAuthenticated: Boolean(token),
      isCheckingSession,
      sessionExpired,
      dismissSessionExpired,
      email,
      name,
      roles,
      isAdmin: roles.includes('admin'),
      hasRole: (role) => roles.includes(String(role).toLowerCase()),
      syncRoles,
      login,
      completeSignup,
      setName: setNameOnly,
      logout,
    }),
    [
      token,
      isCheckingSession,
      sessionExpired,
      dismissSessionExpired,
      email,
      name,
      roles,
      syncRoles,
      login,
      completeSignup,
      setNameOnly,
      logout,
    ]
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
      <SessionExpiredDialog open={sessionExpired} onClose={dismissSessionExpired} />
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
