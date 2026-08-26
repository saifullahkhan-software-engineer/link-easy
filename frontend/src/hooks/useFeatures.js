import { useEffect, useState } from 'react';

import { featuresApi } from '../api/endpoints';

/**
 * Deployment feature flags, fetched once and cached for the tab's lifetime.
 *
 * LinkedIn automation is disabled until each account can be given a
 * residential proxy — LinkedIn blocks sign-ins from datacenter IPs — so the
 * UI must not offer forms that cannot succeed. WhatsApp is unaffected.
 *
 * Fails OPEN for WhatsApp and CLOSED for LinkedIn: if the flags endpoint is
 * unreachable we keep LinkedIn hidden rather than showing a form that would
 * 503 anyway.
 */

const FALLBACK = {
  linkedin: {
    enabled: false,
    message:
      'LinkedIn automation is temporarily unavailable while we add proxy support.',
  },
  whatsapp: { enabled: true, message: null },
  // Assume scheduling works when we cannot reach the backend: hiding a
  // working feature is worse than showing one that will return a clear 503.
  scheduled_jobs: { enabled: true, message: null },
  // Never claim to be the hosted demo on a failed fetch — a self-hosted user
  // should not be told to "run it locally".
  deployment: { is_demo: false, notice: null, support_email: null },
};

// Module-level cache — the flags cannot change without a backend restart, so
// there is no reason for every page to refetch them.
let cached = null;
let inFlight = null;

export function useFeatures() {
  const [features, setFeatures] = useState(cached);
  const [loading, setLoading] = useState(!cached);

  useEffect(() => {
    if (cached) return undefined;

    let active = true;
    inFlight =
      inFlight ||
      featuresApi
        .get()
        .then(({ data }) => {
          cached = data;
          return data;
        })
        .catch(() => {
          // Never surface an error for this — degrade to the safe defaults.
          cached = FALLBACK;
          return FALLBACK;
        })
        .finally(() => {
          inFlight = null;
        });

    inFlight.then((data) => {
      if (!active) return;
      setFeatures(data);
      setLoading(false);
    });

    return () => {
      active = false;
    };
  }, []);

  const resolved = features || FALLBACK;
  return {
    features: resolved,
    loading,
    linkedinEnabled: Boolean(resolved?.linkedin?.enabled),
    linkedinMessage: resolved?.linkedin?.message || FALLBACK.linkedin.message,
    // Timer-driven work. Defaults to true so an older backend that does not
    // yet return this key keeps showing the scheduling UI.
    scheduledJobsEnabled: resolved?.scheduled_jobs?.enabled !== false,
    scheduledJobsMessage: resolved?.scheduled_jobs?.message || null,
    // Hosted demo. The banner keys off isDemo, so it appears ONLY when the
    // backend reports ENVIRONMENT=deployment.
    isDemo: Boolean(resolved?.deployment?.is_demo),
    deploymentNotice: resolved?.deployment?.notice || null,
    supportEmail: resolved?.deployment?.support_email || null,
  };
}

export default useFeatures;
