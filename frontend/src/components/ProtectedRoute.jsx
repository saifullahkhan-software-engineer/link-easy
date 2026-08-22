import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { Spinner } from './Spinner';

export default function ProtectedRoute({ children }) {
  const { isAuthenticated, isCheckingSession } = useAuth();
  const location = useLocation();

  // While the boot-time session check is refreshing an expired access token,
  // hold the route in a neutral loading state. This is what keeps a page
  // refresh with a valid (refreshable) session error-free: protected pages —
  // and their API calls — don't mount until the session verdict is in.
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
  return children;
}
