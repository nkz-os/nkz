// Optional analytics: inject only after `analyticsEnabled` (see CookieConsentContext).

import React, { useEffect } from 'react';
import { useCookieConsentOptional } from '@/context/CookieConsentContext';

export const AnalyticsConsentRoot: React.FC = () => {
  const consent = useCookieConsentOptional();

  useEffect(() => {
    if (!consent?.analyticsEnabled) return;
    // ASSUMPTION: When enabling first-party or third-party analytics, inject only here
    // after consent — never from index.html without this gate.
  }, [consent?.analyticsEnabled]);

  return null;
};
