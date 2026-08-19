import { useCallback, useEffect, useState } from 'react';
import { adminApi } from '../api/endpoints';
import { useAuth } from '../context/AuthContext';

/**
 * Decides whether to offer the Admin Dashboard to the current user.
 *
 * Two sources, deliberately:
 *   1. The `roles` claim in the access token — instant, no request, so the
 *      button does not flicker in on every page load.
 *   2. `GET /api/v1/admin/me` — authoritative, and also reports whether the
 *      backend is enforcing admin access yet.
 *
 * While `admin_api_enforced` is false the app is in bootstrap mode: the
 * developer has not assigned roles yet, so every signed-in user is shown the
 * admin entry point (otherwise nobody could reach the screen that assigns the
 * first admin). Flip `ADMIN_API_ENFORCED=true` in the backend env once the
 * roles are set and the button becomes admin-only.
 */
export function useAdminAccess() {
  const { isAuthenticated, isAdmin: isAdminFromToken } = useAuth();

  const [state, setState] = useState({
    isAdmin: isAdminFromToken,
    enforced: true,
    loading: isAuthenticated,
    checked: false,
  });

  const refresh = useCallback(async () => {
    if (!isAuthenticated) {
      setState({ isAdmin: false, enforced: true, loading: false, checked: true });
      return;
    }
    try {
      const { data } = await adminApi.me();
      setState({
        isAdmin: Boolean(data?.is_admin),
        enforced: Boolean(data?.admin_api_enforced),
        loading: false,
        checked: true,
      });
    } catch {
      // Offline or an old backend without /admin/me: fall back to the token
      // claim rather than hiding navigation the user may legitimately have.
      setState({
        isAdmin: isAdminFromToken,
        enforced: true,
        loading: false,
        checked: true,
      });
    }
  }, [isAuthenticated, isAdminFromToken]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    ...state,
    // The single flag the UI should branch on.
    canSeeAdmin: isAuthenticated && (state.isAdmin || !state.enforced),
    refresh,
  };
}

export default useAdminAccess;
