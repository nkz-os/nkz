// =============================================================================
// Cookie consent — first layer + configure (LSSI transparency / RGPD UX)
// =============================================================================

import React, { useEffect, useState } from 'react';
import { Cookie } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import { Button, Input } from '@nekazari/ui-kit';
import {

/* eslint-disable @typescript-eslint/no-explicit-any */
  useCookieConsent,
  COOKIE_POLICY_NOTICE_VERSION,
} from '@/context/CookieConsentContext';

export const CookieBanner: React.FC = () => {
  const { t } = useI18n();
  const {
    hasAnswered,
    preferences,
    preferencesOpen,
    closePreferences,
    acceptAll,
    rejectOptional,
    saveCustom,
  } = useCookieConsent();

  const [showConfigure, setShowConfigure] = useState(false);
  const [draftAnalytics, setDraftAnalytics] = useState(preferences.analytics);

  useEffect(() => {
    setDraftAnalytics(preferences.analytics);
  }, [preferences.analytics, preferencesOpen]);

  useEffect(() => {
    if (preferencesOpen) {
      setShowConfigure(true);
    }
  }, [preferencesOpen]);

  const visible = !hasAnswered || preferencesOpen;
  if (!visible) return null;

  const policyHref = t('cookies.policy_href');

  const handleSaveCustom = () => {
    saveCustom(draftAnalytics);
    setShowConfigure(false);
  };

  const handleReject = () => {
    rejectOptional();
    setShowConfigure(false);
  };

  const handleAcceptAll = () => {
    acceptAll();
    setShowConfigure(false);
  };

  const dismissOverlay =
    hasAnswered && preferencesOpen ? (
      <Button
        type="button"
        className="fixed inset-0 z-40 bg-black/40"
        aria-label={t('cookies.close_overlay')}
        onClick={() => {
          closePreferences();
          setShowConfigure(false);
        }}
      />
    ) : null;

  return (
    <>
      {dismissOverlay}
      <div
        className={`fixed bottom-0 left-0 right-0 z-50 border-t-2 border-nkz-border bg-white shadow-lg dark:border-gray-700 dark:bg-gray-900 ${
          hasAnswered && preferencesOpen ? 'max-h-[90vh] overflow-y-auto' : ''
        }`}
        role="dialog"
        aria-modal={hasAnswered && preferencesOpen ? 'true' : undefined}
        aria-labelledby="nkz-cookie-title"
      >
        <div className="max-w-7xl mx-auto px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex flex-col gap-4">
            <div className="flex items-start gap-3 flex-1">
              <Cookie className="w-6 h-6 text-nkz-success flex-shrink-0 mt-1 dark:text-green-400" />
              <div className="flex-1 min-w-0">
                <h3
                  id="nkz-cookie-title"
                  className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-1"
                >
                  {t('cookies.title')}
                </h3>
                <p className="text-sm text-gray-600 dark:text-nkz-muted">
                  {t('cookies.message')}
                </p>
                <p className="text-xs text-nkz-muted dark:text-nkz-muted mt-2">
                  {t('cookies.session_note')}
                </p>
                <p className="text-xs text-nkz-muted mt-1">
                  <span className="font-medium">{t('cookies.policy_label')} </span>
                  <a
                    href={policyHref}
                    className="text-nkz-success hover:text-green-800 dark:text-green-400 font-semibold underline"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {t('cookies.learn_more')}
                  </a>
                  {' · '}
                  <span className="text-nkz-muted">
                    {t('cookies.policy_version', { version: COOKIE_POLICY_NOTICE_VERSION })}
                  </span>
                </p>
              </div>
            </div>

            {showConfigure && (
              <div className="rounded-lg border border-nkz-border dark:border-gray-700 p-4 bg-nkz-bg-secondary dark:bg-gray-800/50 space-y-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {t('cookies.category_necessary_title')}
                    </p>
                    <p className="text-xs text-gray-600 dark:text-nkz-muted mt-1">
                      {t('cookies.category_necessary_desc')}
                    </p>
                  </div>
                  <span className="text-xs font-semibold text-nkz-muted uppercase shrink-0">
                    {t('cookies.always_active')}
                  </span>
                </div>
                <div className="flex items-start justify-between gap-4 border-t border-nkz-border dark:border-gray-600 pt-4">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {t('cookies.category_analytics_title')}
                    </p>
                    <p className="text-xs text-gray-600 dark:text-nkz-muted mt-1">
                      {t('cookies.category_analytics_desc')}
                    </p>
                  </div>
                  <Input
                    type="checkbox"
                    className="mt-1 h-5 w-5 shrink-0 rounded border-nkz-border text-nkz-success focus:ring-green-500 dark:border-gray-600 dark:bg-gray-700"
                    checked={draftAnalytics}
                    onChange={(e: any) => setDraftAnalytics(e.target.checked)}
                    aria-label={t('cookies.category_analytics_title')}
                  />
                </div>
              </div>
            )}

            <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-end gap-3">
              {!showConfigure ? (
                <>
                  <Button
                    type="button"
                    onClick={handleReject}
                    className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-nkz-border rounded-lg hover:bg-nkz-bg-secondary dark:bg-gray-800 dark:text-gray-200 dark:border-gray-600 dark:hover:bg-gray-700"
                  >
                    {t('cookies.reject_optional')}
                  </Button>
                  <Button
                    type="button"
                    onClick={() => setShowConfigure(true)}
                    className="px-4 py-2 text-sm font-medium text-gray-800 bg-nkz-bg-secondary border border-nkz-border rounded-lg hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-100 dark:border-gray-600"
                  >
                    {t('cookies.configure')}
                  </Button>
                  <Button
                    type="button"
                    onClick={handleAcceptAll}
                    className="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700"
                  >
                    {t('cookies.accept_all')}
                  </Button>
                </>
              ) : (
                <>
                  <Button
                    type="button"
                    onClick={() => {
                      setShowConfigure(false);
                      if (preferencesOpen) closePreferences();
                    }}
                    className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-nkz-border rounded-lg hover:bg-nkz-bg-secondary dark:bg-gray-800 dark:text-gray-200"
                  >
                    {t('cookies.cancel')}
                  </Button>
                  <Button
                    type="button"
                    onClick={handleSaveCustom}
                    className="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700"
                  >
                    {t('cookies.save_preferences')}
                  </Button>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
};
