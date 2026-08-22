import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useAdminAccess } from '../hooks/useAdminAccess';
import { Spinner } from './Spinner';

/**
 * Route guard for the admin area.
 *
 * Signed out -> /login. Signed in but not an admin (once the backend is
 * enforcing roles) -> the app dashboard, because bouncing a customer to the
 * login page would look like a broken session rather than a permissions
 * boundary. The API is the real authority; this only avoids rendering a
 * screen the user cannot use.
 */
export default function AdminRoute({ children }) {
  const { isAuthenticated, isCheckingSession } = useAuth();
  const { canSeeAdmin, loading } = useAdminAccess();
  const location = useLocation();

  // Same boot-time gate as ProtectedRoute: never bounce to /login while the
  // session is still being validated/renewed after a page refresh.
  if (isCheckingSession) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-surface-950 text-zinc-400">
        <Spinner className="h-6 w-6 text-accent-400" />
        <p className="text-sm">Restoring your session…</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }

  // Wait for /admin/me before deciding, so an admin never gets a redirect
  // flash on a hard refresh.
  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (!canSeeAdmin) {
    return <Navigate to="/app" replace />;
  }

  return children;
}
