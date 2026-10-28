// =============================================================================
// Development-environment notice — opt-in via runtime VITE_PLATFORM_ENV_NOTICE
// =============================================================================

import React, { useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import { useAuth } from '@/context/KeycloakAuthContext';
import { isDevelopmentEnvNotice } from '@/config/environment';
import { Button } from '@nekazari/ui-kit';

const STORAGE_KEY = 'nkz_env_notice_collapsed';

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

function writeCollapsed(value: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? '1' : '0');
  } catch {
    /* storage unavailable: preference lasts for this page load only */
  }
}

export const EnvNotice: React.FC = () => {
  const { t } = useI18n();
  const { isAuthenticated } = useAuth();
  const [collapsed, setCollapsed] = useState<boolean>(readCollapsed);

  if (!isDevelopmentEnvNotice() || !isAuthenticated) return null;

  const setAndStore = (value: boolean) => {
    setCollapsed(value);
    writeCollapsed(value);
  };

  if (collapsed) {
    return (
      <div role="status" className="fixed bottom-2 left-2 z-40">
        <Button
          type="button"
          variant="ghost"
          aria-label={t('layout.env_notice.expand')}
          onClick={() => setAndStore(false)}
          className="flex items-center gap-1 rounded-full border border-yellow-200 bg-nkz-warning-soft px-2 py-1 text-xs font-semibold text-amber-700 shadow"
        >
          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
          DEV
        </Button>
      </div>
    );
  }

  return (
    <div
      role="status"
      className="fixed top-0 left-0 right-0 z-40 flex items-center justify-center gap-2 border-b border-yellow-200 bg-nkz-warning-soft px-3 py-1 text-xs text-amber-700"
    >
      <AlertTriangle className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
      <span className="min-w-0">{t('layout.env_notice.message')}</span>
      <Button
        type="button"
        variant="ghost"
        aria-label={t('layout.env_notice.collapse')}
        onClick={() => setAndStore(true)}
        className="flex-shrink-0 p-0.5"
      >
        <X className="h-4 w-4" aria-hidden="true" />
      </Button>
    </div>
  );
};
