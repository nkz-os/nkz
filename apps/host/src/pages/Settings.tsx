// =============================================================================
// Settings Page - User Profile and Tenant Configuration
// =============================================================================

import React, { useState, useEffect } from 'react';
import { Layout } from '@/components/Layout';
import { useAuth } from '@/context/KeycloakAuthContext';
import { useI18n } from '@/context/I18nContext';
import { LanguageSelector } from '@/components/LanguageSelector';
import { ExternalApiCredentials } from '@/components/ExternalApiCredentials';
import { CopernicusCredentials } from '@/components/CopernicusCredentials';
import { TenantUsersManagement } from '@/components/TenantUsersManagement';
import { ModuleVisibilitySettings } from '@/components/ModuleVisibilitySettings';
import { RiskAlertSubscriptions } from '@/components/RiskAlertSubscriptions';
import { RiskWebhooksPanel } from '@/components/RiskWebhooksPanel';
import api from '@/services/api';
import { getConfig } from '@/config/environment';
import { TenantProfileEditor } from '@/components/settings/TenantProfileEditor';
import { Copy, Check, Edit2, Save, X } from 'lucide-react';
import { useCookieConsent } from '@/context/CookieConsentContext';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';


/* eslint-disable @typescript-eslint/no-explicit-any */
export const Settings: React.FC = () => {
  const { user, tenantId, tenantName, tenantProfile, hasRole, hasAnyRole } = useAuth();
  const { t } = useI18n();
  const { openPreferences } = useCookieConsent();

  const canModifySettings = hasAnyRole(['PlatformAdmin', 'TenantAdmin']);
  const canManageUsers = hasAnyRole(['PlatformAdmin', 'TenantAdmin']);
  const isReadOnly = hasAnyRole(['TechnicalConsultant']) && !canModifySettings;
  const canViewRisks = hasAnyRole(['PlatformAdmin', 'TenantAdmin', 'TechnicalConsultant']);
  const canManageModuleVisibility = hasAnyRole(['PlatformAdmin', 'TenantAdmin']);
  const isPlatformAdmin = hasRole('PlatformAdmin');

  const [copiedTenantId, setCopiedTenantId] = useState(false);

  // User profile editing
  const [isEditingName, setIsEditingName] = useState(false);
  const [editedFirstName, setEditedFirstName] = useState('');
  const [editedLastName, setEditedLastName] = useState('');
  const [savingName, setSavingName] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);
  const [nameSuccess, setNameSuccess] = useState<string | null>(null);

  const currentTenantId = tenantId || user?.tenant || 'N/A';

  useEffect(() => {
    if (user) {
      const nameParts = (user.name || '').split(' ');
      setEditedFirstName(user.firstName || nameParts[0] || '');
      setEditedLastName(user.lastName || (nameParts.length > 1 ? nameParts.slice(1).join(' ') : ''));
    }
  }, [user]);

  const handleCopyTenantId = () => {
    if (currentTenantId && currentTenantId !== 'N/A') {
      navigator.clipboard.writeText(currentTenantId);
      setCopiedTenantId(true);
      setTimeout(() => setCopiedTenantId(false), 2000);
    }
  };

  const handleStartEditName = () => {
    setIsEditingName(true);
    setNameError(null);
    setNameSuccess(null);
  };

  const handleCancelEditName = () => {
    setIsEditingName(false);
    if (user) {
      const nameParts = (user.name || '').split(' ');
      setEditedFirstName(user.firstName || nameParts[0] || '');
      setEditedLastName(user.lastName || (nameParts.length > 1 ? nameParts.slice(1).join(' ') : ''));
    }
    setNameError(null);
    setNameSuccess(null);
  };

  const handleSaveName = async () => {
    if (!editedFirstName.trim()) {
      setNameError(t('settings.profile.name_required'));
      return;
    }

    setSavingName(true);
    setNameError(null);
    setNameSuccess(null);

    try {
      await api.updateUserProfile(editedFirstName.trim(), editedLastName.trim());
      setNameSuccess(t('settings.profile.name_updated'));
      setIsEditingName(false);
      setTimeout(() => window.location.reload(), 1500);
    } catch (err: any) {
      const errorMessage = err?.response?.data?.error || err?.response?.data?.message || err?.message || t('settings.profile.name_error');
      setNameError(errorMessage);
      if (import.meta.env.DEV) logger.error('Error updating user name:', err);
    } finally {
      setSavingName(false);
    }
  };

  return (
    <Layout>
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{t('settings.title')}</h1>
            <p className="text-gray-600">{t('settings.subtitle')}</p>
          </div>
          <LanguageSelector />
        </div>

        <div className="bg-white rounded-lg shadow-sm border border-nkz-border p-6 mb-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-2">{t('settings.cookies_title')}</h2>
          <p className="text-sm text-gray-600 mb-4">{t('settings.cookies_description')}</p>
          <Button
            type="button"
            onClick={openPreferences}
            variant="primary"
            size="sm"
            className="text-sm"
          >
            {t('settings.cookies_manage')}
          </Button>
        </div>

        {/* User Profile Card */}
        <div className="bg-white rounded-lg shadow-sm border border-nkz-border p-6 mb-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">{t('settings.account_info')}</h2>
          {nameError && (
            <div className="mb-4 p-3 bg-nkz-error-light border border-red-200 rounded-lg">
              <p className="text-sm text-red-800">{nameError}</p>
            </div>
          )}
          {nameSuccess && (
            <div className="mb-4 p-3 bg-nkz-success-soft border border-nkz-success rounded-lg">
              <p className="text-sm text-nkz-success-strong">{nameSuccess}</p>
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium text-nkz-muted">{t('settings.name')}</label>
              {isEditingName ? (
                <div className="space-y-2">
                  <div className="flex gap-2">
                    <Input
                      type="text"
                      value={editedFirstName}
                      onChange={(e: any) => setEditedFirstName(e.target.value)}
                      placeholder={t('settings.profile.first_name')}
                      className="flex-1 px-3 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      disabled={savingName}
                    />
                    <Input
                      type="text"
                      value={editedLastName}
                      onChange={(e: any) => setEditedLastName(e.target.value)}
                      placeholder={t('settings.profile.last_name')}
                      className="flex-1 px-3 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      disabled={savingName}
                    />
                  </div>
                  <div className="flex gap-2">
                    <Button
                      onClick={handleSaveName}
                      disabled={savingName}
                      className="flex items-center gap-2 px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition disabled:opacity-50 text-sm"
                    >
                      <Save className="w-4 h-4" />
                      {savingName ? t('settings.saving') : t('settings.save')}
                    </Button>
                    <Button
                      onClick={handleCancelEditName}
                      disabled={savingName}
                      className="flex items-center gap-2 px-3 py-1.5 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition disabled:opacity-50 text-sm"
                    >
                      <X className="w-4 h-4" />
                      {t('settings.cancel')}
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <p className="text-gray-900 flex-1">
                    {(user?.firstName || editedFirstName || '').trim() && (user?.lastName || editedLastName || '').trim()
                      ? `${user?.firstName || editedFirstName || ''} ${user?.lastName || editedLastName || ''}`
                      : (user?.firstName || editedFirstName || user?.lastName || editedLastName || t('settings.profile.not_set'))}
                  </p>
                  <Button
                    onClick={handleStartEditName}
                    className="flex items-center gap-1 text-nkz-info hover:text-nkz-info transition text-sm font-medium"
                    title={t('settings.profile.edit_name')}
                  >
                    <Edit2 className="w-4 h-4" />
                    {t('settings.profile.edit')}
                  </Button>
                </div>
              )}
            </div>
            <div>
              <label className="text-sm font-medium text-nkz-muted">{t('settings.email')}</label>
              <p className="text-gray-900">{user?.email}</p>
            </div>
            <div>
              <label className="text-sm font-medium text-nkz-muted">{t('settings.farm')}</label>
              <p className="text-gray-900">{tenantName || tenantId || 'N/A'}</p>
            </div>
            <div>
              <label className="text-sm font-medium text-nkz-muted">{t('settings.tenant_id')}</label>
              <div className="flex items-center gap-2">
                <p className="text-gray-900 font-mono text-sm flex-1">{currentTenantId}</p>
                {currentTenantId !== 'N/A' && (
                  <Button
                    onClick={handleCopyTenantId}
                    className="text-nkz-muted hover:text-gray-600 transition"
                    title={t('settings.copy_tenant_id')}
                  >
                    {copiedTenantId ? (
                      <Check className="w-4 h-4 text-nkz-success-strong" />
                    ) : (
                      <Copy className="w-4 h-4" />
                    )}
                  </Button>
                )}
              </div>
            </div>
            <div>
              <label className="text-sm font-medium text-nkz-muted">{t('settings.roles', { defaultValue: 'Roles' })}</label>
              <div className="flex flex-wrap gap-1 mt-1">
                {(user?.roles || []).length > 0 ? (user?.roles || []).map((role: string) => (
                  <span key={role} className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    role === 'PlatformAdmin' ? 'bg-purple-100 text-purple-800' :
                    role === 'TenantAdmin' ? 'bg-nkz-info-soft text-blue-800' :
                    role === 'GestorCUE' ? 'bg-rose-100 text-rose-800' :
                    role === 'TechnicalConsultant' ? 'bg-nkz-success-soft text-green-800' :
                    role === 'Farmer' ? 'bg-nkz-warning-soft text-yellow-800' :
                    role === 'role_pro_expired' ? 'bg-nkz-error-light text-red-800' :
                    'bg-nkz-bg-secondary text-gray-600'
                  }`}>
                    {role === 'role_pro_expired' ? 'Expired' : role}
                  </span>
                )) : (
                  <span className="text-sm text-nkz-muted">{t('settings.no_roles', { defaultValue: 'No roles assigned' })}</span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Subscription & Plan Info */}
        {tenantProfile && (
          <div className="bg-white rounded-lg shadow-sm border border-nkz-border p-6 mb-6">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">{t('settings.subscription.title', { defaultValue: 'Plan & Subscription' })}</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="text-sm font-medium text-nkz-muted">{t('settings.subscription.plan', { defaultValue: 'Plan' })}</label>
                <p className="text-gray-900 font-semibold capitalize">{tenantProfile.plan_type}</p>
              </div>
              <div>
                <label className="text-sm font-medium text-nkz-muted">{t('settings.subscription.status', { defaultValue: 'Status' })}</label>
                <p className={`font-semibold ${tenantProfile.status === 'active' ? 'text-nkz-success-strong' : 'text-nkz-error'}`}>
                  {tenantProfile.status}
                </p>
              </div>
              <div>
                <label className="text-sm font-medium text-nkz-muted">{t('settings.subscription.expires', { defaultValue: 'Expires' })}</label>
                <p className="text-gray-900">
                  {tenantProfile.expires_at
                    ? new Date(tenantProfile.expires_at).toLocaleDateString()
                    : 'N/A'}
                </p>
              </div>
              <div>
                <label className="text-sm font-medium text-nkz-muted">{t('settings.subscription.max_users', { defaultValue: 'Max Users' })}</label>
                <p className="text-gray-900">{tenantProfile.max_users}</p>
              </div>
              <div>
                <label className="text-sm font-medium text-nkz-muted">{t('settings.subscription.max_robots', { defaultValue: 'Max Robots' })}</label>
                <p className="text-gray-900">{tenantProfile.max_robots}</p>
              </div>
              <div>
                <label className="text-sm font-medium text-nkz-muted">{t('settings.subscription.max_sensors', { defaultValue: 'Max Sensors' })}</label>
                <p className="text-gray-900">{tenantProfile.max_sensors}</p>
              </div>
            </div>
            {user?.roles?.includes('role_pro_expired') && (
              <div className="mt-4 bg-amber-50 border border-amber-200 rounded-lg p-3">
                <p className="text-sm text-amber-800 font-semibold">
                  {t('settings.subscription.expired_banner', { defaultValue: 'Your subscription has expired. Read-only mode is active.' })}
                </p>
                {getConfig().external.billingUrl && (
                  <a
                    href={getConfig().external.billingUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-block mt-2 px-3 py-1.5 bg-amber-600 text-white text-xs font-medium rounded-lg hover:bg-amber-700 transition"
                  >
                    {t('dashboard.renew_subscription', { defaultValue: 'Renew' })}
                  </a>
                )}
              </div>
            )}
          </div>
        )}

        {/* Tenant Profile Editor */}
        {canModifySettings && (
          <div className="mb-6">
            <TenantProfileEditor />
          </div>
        )}

        {/* Copernicus BYOK — Tenant self-service */}
        {canModifySettings && (
          <div className="mb-6">
            <CopernicusCredentials />
          </div>
        )}

        {/* External API Credentials — PlatformAdmin only */}
        {isPlatformAdmin && (
          <div className="mb-6">
            <ExternalApiCredentials />
          </div>
        )}

        {/* Risk Alert Subscriptions */}
        {canViewRisks && (
          <div className="mb-6">
            <RiskAlertSubscriptions readOnly={isReadOnly} />
          </div>
        )}

        {/* Risk Webhooks */}
        {canViewRisks && (
          <div className="mb-6">
            <RiskWebhooksPanel readOnly={isReadOnly} />
          </div>
        )}

        {/* Tenant Users Management */}
        {canManageUsers && (
          <div className="mb-6">
            <TenantUsersManagement canManageUsers={canManageUsers} />
          </div>
        )}

        {/* Module visibility by role (tenant-specific) */}
        {canManageModuleVisibility && (
          <div className="mb-6">
            <ModuleVisibilitySettings />
          </div>
        )}

        {/* Read-only mode info for TechnicalConsultant */}
        {isReadOnly && (
          <div className="mb-6 bg-nkz-info-soft border border-blue-200 rounded-lg p-4">
            <p className="text-blue-800 text-sm">
              <strong>{t('settings.read_only_mode')}:</strong> {t('settings.read_only_description')}
            </p>
          </div>
        )}

        {/* Documentation Links */}
        <div className="mt-6 space-y-2">
          <div className="text-center">
            <p className="text-sm text-gray-600 mb-3">{t('settings.docs.title')}</p>
            <div className="flex flex-wrap justify-center gap-4">
              <a
                href="https://github.com/nkz-os/nkz/blob/main/docs/api/01-getting-started.md"
                target="_blank"
                rel="noopener noreferrer"
                className="text-nkz-info hover:text-nkz-info text-sm font-medium underline"
              >
                {t('settings.docs.getting_started')}
              </a>
              <span className="text-nkz-muted">|</span>
              <a
                href="https://github.com/nkz-os/nkz/blob/main/docs/api/devices/iot-devices.md"
                target="_blank"
                rel="noopener noreferrer"
                className="text-nkz-info hover:text-nkz-info text-sm font-medium underline"
              >
                {t('settings.docs.iot_devices')}
              </a>
              <span className="text-nkz-muted">|</span>
              <a
                href="https://github.com/nkz-os/nkz/blob/main/docs/api/devices/weather-stations.md"
                target="_blank"
                rel="noopener noreferrer"
                className="text-nkz-info hover:text-nkz-info text-sm font-medium underline"
              >
                {t('settings.docs.weather_stations')}
              </a>
            </div>
          </div>
        </div>
      </div>
    </Layout>
  );
};

export default Settings;
