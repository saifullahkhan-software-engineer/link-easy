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
  const { isAuthenticated } = useAuth();
  const { canSeeAdmin, loading } = useAdminAccess();
  const location = useLocation();

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
