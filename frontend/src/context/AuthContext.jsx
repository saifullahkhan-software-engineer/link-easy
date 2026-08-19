import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import {
  clearSession,
  getAccessToken,
  getUserEmail,
  getUserName,
  getUserRoles,
  storeSession,
} from '../api/client';
import { authApi } from '../api/endpoints';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => getAccessToken());
  const [email, setEmail] = useState(() => getUserEmail());
  const [name, setName] = useState(() => getUserName());
  // Roles come from the access token, so they refresh whenever the token does
  // and cannot drift out of sync with a separately stored copy.
  const [roles, setRoles] = useState(() => getUserRoles());

  const login = useCallback(async (loginEmail, password) => {
    const { data } = await authApi.login(loginEmail, password);
    storeSession({ ...data, email: loginEmail });
    setToken(data.access_token);
    setEmail(loginEmail);
    setName(getUserName());
    setRoles(getUserRoles());
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
    [token, email, name, roles, syncRoles, login, completeSignup, setNameOnly, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
