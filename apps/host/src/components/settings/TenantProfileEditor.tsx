import React, { useState, useEffect } from 'react';
import { useAuth } from '@/context/KeycloakAuthContext';
import { useI18n } from '@/context/I18nContext';
import { Save, MapPin, Globe, Clock, Banknote } from 'lucide-react';
import api from '@/services/api';

export const TenantProfileEditor: React.FC = () => {
  const { tenantProfile, refreshTenantProfile, hasRole } = useAuth();
  const { t } = useI18n();
  const [form, setForm] = useState({
    tenant_name: '',
    timezone: 'Europe/Madrid',
    locale: 'es',
    currency: 'EUR',
    default_lat: '',
    default_lon: '',
  });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const canEdit = hasRole('TenantAdmin') || hasRole('PlatformAdmin');

  useEffect(() => {
    if (tenantProfile) {
      setForm({
        tenant_name: tenantProfile.tenant_name || '',
        timezone: tenantProfile.timezone || 'Europe/Madrid',
        locale: tenantProfile.locale || 'es',
        currency: tenantProfile.currency || 'EUR',
        default_lat: tenantProfile.default_location?.lat?.toString() || '',
        default_lon: tenantProfile.default_location?.lon?.toString() || '',
      });
    }
  }, [tenantProfile]);

  const handleSave = async () => {
    if (!canEdit) return;
    setSaving(true);
    setMessage(null);

    try {
      const payload: any = {
        tenant_name: form.tenant_name.trim(),
        timezone: form.timezone,
        locale: form.locale,
        currency: form.currency,
      };

      if (form.default_lat && form.default_lon) {
        payload.default_location = {
          lat: parseFloat(form.default_lat),
          lon: parseFloat(form.default_lon),
        };
      } else {
        payload.default_location = null;
      }

      await api.patch('/api/tenant/profile', payload);
      refreshTenantProfile();
      setMessage({ type: 'success', text: t('settings.profile_updated') });
    } catch (err: any) {
      const errorMsg = err.response?.data?.error || t('common.error');
      setMessage({ type: 'error', text: errorMsg });
    } finally {
      setSaving(false);
    }
  };

  if (!canEdit) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-xl p-6">
        <p className="text-gray-500">{t('settings.admin_only')}</p>
      </div>
    );
  }

  const timezones = [
    'Europe/Madrid', 'Europe/London', 'Europe/Paris', 'Europe/Berlin',
    'Europe/Rome', 'Europe/Lisbon', 'Europe/Brussels', 'Europe/Amsterdam',
    'Atlantic/Canary',
  ];

  const locales = [
    { value: 'es', label: 'Castellano' },
    { value: 'en', label: 'English' },
    { value: 'eu', label: 'Euskara' },
    { value: 'ca', label: 'Catala' },
    { value: 'fr', label: 'Francais' },
    { value: 'pt', label: 'Portugues' },
  ];

  const currencies = [
    { value: 'EUR', label: 'EUR' },
    { value: 'GBP', label: 'GBP' },
  ];

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
      <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-6">
        {t('settings.tenant_profile')}
      </h2>

      <div className="space-y-4">
        {/* Farm Name */}
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            {t('settings.farm_name')}
          </label>
          <input
            type="text"
            value={form.tenant_name}
            onChange={e => setForm(f => ({ ...f, tenant_name: e.target.value }))}
            maxLength={100}
            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Timezone */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              <Clock className="w-4 h-4 inline mr-1" />
              {t('settings.timezone')}
            </label>
            <select
              value={form.timezone}
              onChange={e => setForm(f => ({ ...f, timezone: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
            >
              {timezones.map(tz => (
                <option key={tz} value={tz}>{tz}</option>
              ))}
            </select>
          </div>

          {/* Locale */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              <Globe className="w-4 h-4 inline mr-1" />
              {t('settings.language')}
            </label>
            <select
              value={form.locale}
              onChange={e => setForm(f => ({ ...f, locale: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
            >
              {locales.map(l => (
                <option key={l.value} value={l.value}>{l.label}</option>
              ))}
            </select>
          </div>

          {/* Currency */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              <Banknote className="w-4 h-4 inline mr-1" />
              {t('settings.currency')}
            </label>
            <select
              value={form.currency}
              onChange={e => setForm(f => ({ ...f, currency: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
            >
              {currencies.map(c => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Default Location */}
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            <MapPin className="w-4 h-4 inline mr-1" />
            {t('settings.default_location')}
          </label>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="number"
              step="0.0001"
              placeholder={t('settings.latitude')}
              value={form.default_lat}
              onChange={e => setForm(f => ({ ...f, default_lat: e.target.value }))}
              className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
            />
            <input
              type="number"
              step="0.0001"
              placeholder={t('settings.longitude')}
              value={form.default_lon}
              onChange={e => setForm(f => ({ ...f, default_lon: e.target.value }))}
              className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
            />
          </div>
          <p className="text-xs text-gray-500 mt-1">{t('settings.location_hint')}</p>
        </div>

        {/* Save Button + Message */}
        <div className="flex items-center gap-4 pt-2">
          <button
            onClick={handleSave}
            disabled={saving || !form.tenant_name.trim()}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition flex items-center gap-2"
          >
            <Save className="w-4 h-4" />
            {saving ? t('common.saving') : t('common.save')}
          </button>

          {message && (
            <span className={`text-sm ${message.type === 'success' ? 'text-green-600' : 'text-red-600'}`}>
              {message.text}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
