import { Link } from 'react-router-dom';
import { useFeatures } from '../hooks/useFeatures';
import LinkedInUnavailableNotice from './LinkedInUnavailableNotice';
import { Spinner } from './Spinner';

/**
 * Route guard for pages that are useless without LinkedIn automation.
 *
 * Wrapping at the route level (rather than adding an early return inside each
 * page) means the page component never mounts while the feature is off, so its
 * status/chat/message polling intervals never start and never hammer endpoints
 * that can only answer 503.
 *
 * While the flag state is still loading we render a spinner instead of the
 * notice, so a slow /features call can't flash "coming soon" at users on a
 * deployment where LinkedIn is actually enabled.
 */
export default function LinkedInFeatureRoute({ title, children }) {
  const { linkedinEnabled, linkedinMessage, loading } = useFeatures();

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner />
      </div>
    );
  }

  if (!linkedinEnabled) {
    return (
      <div className="max-w-3xl">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-zinc-50">{title}</h1>
            <p className="mt-1 text-sm text-zinc-400">
              LinkedIn automation is temporarily paused on this instance.
            </p>
          </div>
          <Link to="/app/account" className="btn-secondary text-xs">
            ← Accounts
          </Link>
        </div>

        <LinkedInUnavailableNotice message={linkedinMessage} className="mt-6" />
      </div>
    );
  }

  return children;
}
