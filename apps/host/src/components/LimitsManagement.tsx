import React, { useEffect, useState } from 'react';
import { useAuth } from '@/context/KeycloakAuthContext';
import { useI18n } from '@/context/I18nContext';
import api from '@/services/api';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';


type Limits = {
  planType?: string | null;
  maxUsers?: number | null;
  maxRobots?: number | null;
  maxSensors?: number | null;
  maxAreaHectares?: number | null;
  maxParcels?: number | null;
  maxEntitiesTotal?: number | null;
};

export const LimitsManagement: React.FC = () => {
  const { user } = useAuth();
  const { t } = useI18n();
  const [limits, setLimits] = useState<Limits>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const PLAN_TYPES = [
    { value: 'basic', label: t('settings.tier.basic') },
    { value: 'pro', label: t('settings.tier.pro') },
    { value: 'premium', label: t('settings.tier.premium') },
    { value: 'enterprise', label: t('settings.tier.enterprise') },
  ];

  const load = async () => {
    try {
      setLoading(true);
      const tenantId = user?.tenant || 'admin';

      const res = await api.get('/api/admin/tenant-limits', {
        params: { tenant_id: tenantId }
      });
      setLimits({
        planType: res.data.planType ?? res.data.plan ?? '',
        maxUsers: res.data.maxUsers ?? res.data.max_users ?? undefined,
        maxRobots: res.data.maxRobots ?? res.data.max_robots ?? undefined,
        maxSensors: res.data.maxSensors ?? res.data.max_sensors ?? undefined,
        maxAreaHectares: res.data.maxAreaHectares ?? res.data.max_area_hectares ?? undefined,
        maxParcels: res.data.maxParcels ?? res.data.max_parcels ?? undefined,
        maxEntitiesTotal: res.data.maxEntitiesTotal ?? res.data.max_entities_total ?? undefined,
      });
      setMessage(null);
    } catch (e: any) {
      setMessage('Error cargando límites: ' + (e.response?.data?.error || e.message));
      logger.error('Error loading limits:', e);
    } finally {
      setLoading(false);
    }
  };

  const save = async () => {
    try {
      setSaving(true);
      const tenantId = user?.tenant || 'admin';
      const payload: Limits & { tenant_id?: string } = { tenant_id: tenantId };
      if (limits.planType !== undefined) payload.planType = limits.planType || undefined;
      if (limits.maxUsers !== undefined) payload.maxUsers = Number(limits.maxUsers);
      if (limits.maxRobots !== undefined) payload.maxRobots = Number(limits.maxRobots);
      if (limits.maxSensors !== undefined) payload.maxSensors = Number(limits.maxSensors);
      if (limits.maxAreaHectares !== undefined) payload.maxAreaHectares = Number(limits.maxAreaHectares);
      if (limits.maxParcels !== undefined) payload.maxParcels = Number(limits.maxParcels);
      if (limits.maxEntitiesTotal !== undefined) payload.maxEntitiesTotal = Number(limits.maxEntitiesTotal);
      await api.patch('/api/admin/tenant-limits', payload);
      setMessage(t('success'));
      setTimeout(() => setMessage(null), 3000);
      await load();
    } catch (e: any) {
      setMessage('Error guardando límites: ' + (e.response?.data?.error || e.message));
      logger.error('Error saving limits:', e);
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const isBasic = limits.planType === 'basic';

  return (
    <div className="bg-white rounded-lg shadow p-6 mt-8">
      <h2 className="text-lg font-semibold text-gray-900 mb-4">{t('limits')}</h2>
      {message && (
        <div className="mb-4 text-sm text-gray-700">{message}</div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div>
          <label className="block text-sm text-gray-600 mb-1">{t('plan_type')}</label>
          <select
            value={limits.planType ?? ''}
            onChange={(e: any) => setLimits((s) => ({ ...s, planType: e.target.value }))}
            className="w-full border border-nkz-border rounded px-3 py-2"
            disabled={loading}
          >
            <option value="">{t('select_plan')}</option>
            {PLAN_TYPES.map(plan => (
              <option key={plan.value} value={plan.value}>
                {plan.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">{t('max_users')}</label>
          <Input
            type="number"
            value={limits.maxUsers ?? ''}
            onChange={(e: any) => setLimits((s) => ({ ...s, maxUsers: e.target.value === '' ? undefined : Number(e.target.value) }))}
            className="w-full border border-nkz-border rounded px-3 py-2"
            min={0}
            disabled={loading}
          />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">{t('max_robots')}</label>
          <Input
            type="number"
            value={limits.maxRobots ?? ''}
            onChange={(e: any) => setLimits((s) => ({ ...s, maxRobots: e.target.value === '' ? undefined : Number(e.target.value) }))}
            className="w-full border border-nkz-border rounded px-3 py-2"
            min={0}
            disabled={loading}
          />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">{t('max_sensors')}</label>
          <Input
            type="number"
            value={limits.maxSensors ?? ''}
            onChange={(e: any) => setLimits((s) => ({ ...s, maxSensors: e.target.value === '' ? undefined : Number(e.target.value) }))}
            className="w-full border border-nkz-border rounded px-3 py-2"
            min={0}
            disabled={loading}
          />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">{t('max_area_hectares')}</label>
          <Input
            type="number"
            step="0.01"
            value={limits.maxAreaHectares ?? ''}
            onChange={(e: any) => setLimits((s) => ({ ...s, maxAreaHectares: e.target.value === '' ? undefined : Number(e.target.value) }))}
            className="w-full border border-nkz-border rounded px-3 py-2"
            min={0}
            disabled={loading}
          />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">{t('quota.max_parcels')}</label>
          <Input
            type="number"
            value={limits.maxParcels ?? ''}
            onChange={(e: any) => setLimits((s) => ({ ...s, maxParcels: e.target.value === '' ? undefined : Number(e.target.value) }))}
            className="w-full border border-nkz-border rounded px-3 py-2"
            min={0}
            disabled={loading}
          />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">{t('quota.max_entities_total')}</label>
          <Input
            type="number"
            value={limits.maxEntitiesTotal ?? ''}
            onChange={(e: any) => setLimits((s) => ({ ...s, maxEntitiesTotal: e.target.value === '' ? undefined : Number(e.target.value) }))}
            className="w-full border border-nkz-border rounded px-3 py-2"
            min={0}
            disabled={loading || !isBasic}
            title={!isBasic ? t('quota.max_entities_total') : undefined}
          />
          {!isBasic && (
            <p className="text-xs text-nkz-muted mt-1">{t('quota.max_entities_total')}</p>
          )}
        </div>
      </div>
      <div className="mt-6 flex gap-3">
        <Button
          onClick={save}
          disabled={saving || loading}
          className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? t('saving') : t('save')}
        </Button>
        <Button
          onClick={load}
          disabled={loading}
          className="bg-nkz-bg-secondary text-gray-800 px-4 py-2 rounded hover:bg-gray-200 disabled:opacity-50"
        >
          {t('reload')}
        </Button>
      </div>
    </div>
  );
};
