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
  };
}

export default useFeatures;
