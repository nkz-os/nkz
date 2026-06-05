import React, { useState, useEffect } from 'react';
import { useI18n } from '@/context/I18nContext';
import client from '@/services/api';
import { Button, Input } from '@nekazari/ui-kit';

interface TenantConfig {
  tenant_id: string;
  tenant_name: string;
  plan_type: string;
  status: string;
  expires_at: string;
  metadata?: Record<string, unknown>;
}

interface TenantLimits {
  maxUsers?: number | null;
  maxRobots?: number | null;
  maxSensors?: number | null;
  maxAreaHectares?: number | null;
  planType?: string;
}

interface TenantConfigFormProps {
  tenantId: string;
}

const PLAN_DEFAULTS: Record<string, { maxUsers: number; maxRobots: number; maxSensors: number; maxAreaHectares: number | null }> = {
  basic: { maxUsers: 3, maxRobots: 5, maxSensors: 15, maxAreaHectares: 100 },
  premium: { maxUsers: 10, maxRobots: 20, maxSensors: 50, maxAreaHectares: 500 },
  pro: { maxUsers: 25, maxRobots: 50, maxSensors: 200, maxAreaHectares: 2000 },
  enterprise: { maxUsers: 100, maxRobots: 200, maxSensors: 1000, maxAreaHectares: null },
};

export const TenantConfigForm: React.FC<TenantConfigFormProps> = ({ tenantId }) => {
  const { t } = useI18n();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [, setConfig] = useState<TenantConfig | null>(null);
  const [, setLimits] = useState<TenantLimits>({});

  // Editable fields
  const [tenantName, setTenantName] = useState('');
  const [planType, setPlanType] = useState('basic');
  const [status, setStatus] = useState('active');
  const [expiresAt, setExpiresAt] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [contactPhone, setContactPhone] = useState('');

  // Custom limit overrides (null = use plan default)
  const [maxUsers, setMaxUsers] = useState<number | null>(null);
  const [maxRobots, setMaxRobots] = useState<number | null>(null);
  const [maxSensors, setMaxSensors] = useState<number | null>(null);
  const [maxAreaHectares, setMaxAreaHectares] = useState<number | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    loadData();
  }, [tenantId]);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await client.get('/api/admin/tenants');
      const all: Record<string, unknown>[] = Array.isArray(resp.data) ? resp.data : (resp.data?.tenants || []);
      const tdata = all.find((t: any) => (t.tenant_id || t.id) === tenantId);
      if (tdata) {
        const c: TenantConfig = {
          tenant_id: String(tdata.tenant_id ?? tdata.id ?? ''),
          tenant_name: String(tdata.tenant_name ?? tdata.name ?? ''),
          plan_type: String(tdata.plan_type ?? tdata.plan ?? 'basic'),
          status: String(tdata.status ?? 'active'),
          expires_at: String(tdata.expires_at ?? ''),
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          metadata: (tdata as any).metadata || {},
        };
        setConfig(c);
        setTenantName(c.tenant_name);
        setPlanType(c.plan_type);
        setStatus(c.status);
        setExpiresAt(c.expires_at ? c.expires_at.slice(0, 10) : '');
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        setContactEmail(String((c.metadata as any)?.primary_email || (c.metadata as any)?.contact_email || ''));
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        setContactPhone(String((c.metadata as any)?.phone || ''));
      }

      const lr = await client.get(`/api/admin/tenant-limits?tenant_id=${tenantId}`);
      const ld = lr.data || {};
      setLimits(ld);
      setMaxUsers(ld.maxUsers ?? null);
      setMaxRobots(ld.maxRobots ?? null);
      setMaxSensors(ld.maxSensors ?? null);
      setMaxAreaHectares(ld.maxAreaHectares ?? null);
    } catch (err: any) {
      setError(err?.response?.data?.error || err.message || 'Failed to load tenant config');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const metadata: Record<string, string> = {};
      if (contactEmail) metadata.contact_email = contactEmail;
      if (contactPhone) metadata.phone = contactPhone;

      await client.patch(`/api/admin/tenants/${tenantId}`, {
        tenant_name: tenantName,
        plan_type: planType,
        status,
        expires_at: expiresAt || undefined,
        metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
      });

      await client.patch('/api/admin/tenant-limits', {
        tenant_id: tenantId,
        maxUsers,
        maxRobots,
        maxSensors,
        maxAreaHectares,
        planType,
      });

      setSuccess(t('admin.config_saved', { defaultValue: 'Configuration saved.' }));
      setTimeout(() => setSuccess(null), 3000);
    } catch (err: any) {
      setError(err?.response?.data?.error || err.message || 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="p-8 text-center text-nkz-text-secondary">
        {t('common.loading')}
      </div>
    );
  }

  const defaults = PLAN_DEFAULTS[planType] || PLAN_DEFAULTS.basic;

  const limitField = (
    label: string,
    value: number | null,
    setter: (v: number | null) => void,
    defaultVal: number | null,
    key: string
  ) => (
    <div key={key}>
      <label className="block text-nkz-sm font-medium text-nkz-text-secondary mb-1">{label}</label>
      <Input
        type="number"
        value={value ?? ''}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        onChange={(e: any) => {
          const v = e.target.value;
          setter(v === '' ? null : parseInt(v, 10));
        }}
        placeholder={defaultVal != null ? String(defaultVal) : t('common.unlimited')}
        className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none text-nkz-sm bg-nkz-surface"
      />
    </div>
  );

  return (
    <div className="p-6 max-w-2xl">
      <h3 className="text-nkz-lg font-semibold text-nkz-text-primary mb-4">
        {t('admin.tenant_config', { defaultValue: 'Tenant Configuration' })}
      </h3>

      {error && (
        <div className="mb-4 p-3 bg-nkz-danger-soft border border-nkz-danger-soft rounded-nkz-lg text-nkz-sm text-nkz-danger-strong">{error}</div>
      )}
      {success && (
        <div className="mb-4 p-3 bg-nkz-accent-soft border border-nkz-accent-soft rounded-nkz-lg text-nkz-sm text-nkz-accent-strong">{success}</div>
      )}

      <div className="space-y-5">
        {/* Basic info */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-nkz-sm font-medium text-nkz-text-secondary mb-1">
              {t('admin.tenant_name', { defaultValue: 'Tenant Name' })}
            </label>
            <Input
              type="text"
              value={tenantName}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              onChange={(e: any) => setTenantName(e.target.value)}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none text-nkz-sm bg-nkz-surface"
            />
          </div>
          <div>
            <label className="block text-nkz-sm font-medium text-nkz-text-secondary mb-1">
              {t('admin.plan', { defaultValue: 'Plan' })}
            </label>
            <select
              value={planType}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              onChange={(e: any) => setPlanType(e.target.value)}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none text-nkz-sm bg-nkz-surface"
            >
              <option value="basic">{t('common.basic')}</option>
              <option value="premium">{t('common.premium')}</option>
              <option value="pro">{t('settings.tier.pro', { defaultValue: 'Pro' })}</option>
              <option value="enterprise">{t('common.enterprise')}</option>
            </select>
          </div>
          <div>
            <label className="block text-nkz-sm font-medium text-nkz-text-secondary mb-1">
              {t('admin.status', { defaultValue: 'Status' })}
            </label>
            <select
              value={status}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              onChange={(e: any) => setStatus(e.target.value)}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none text-nkz-sm bg-nkz-surface"
            >
              <option value="active">{t('common.active')}</option>
              <option value="suspended">
                {t('admin.status_suspended', { defaultValue: 'Suspended' })}
              </option>
              <option value="inactive">{t('common.inactive')}</option>
            </select>
          </div>
          <div>
            <label className="block text-nkz-sm font-medium text-nkz-text-secondary mb-1">
              {t('admin.expiration_date', { defaultValue: 'Expiration Date' })}
            </label>
            <Input
              type="date"
              value={expiresAt}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              onChange={(e: any) => setExpiresAt(e.target.value)}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none text-nkz-sm bg-nkz-surface"
            />
          </div>
        </div>

        {/* Metadata */}
        <div className="border-t border-nkz-border pt-4">
          <h4 className="text-nkz-sm font-semibold text-nkz-text-primary mb-3">
            {t('admin.contact_info', { defaultValue: 'Contact Info' })}
          </h4>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-nkz-sm font-medium text-nkz-text-secondary mb-1">
                {t('admin.contact_email', { defaultValue: 'Contact Email' })}
              </label>
              <Input
                type="email"
                value={contactEmail}
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                onChange={(e: any) => setContactEmail(e.target.value)}
                className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none text-nkz-sm bg-nkz-surface"
              />
            </div>
            <div>
              <label className="block text-nkz-sm font-medium text-nkz-text-secondary mb-1">
                {t('admin.contact_phone', { defaultValue: 'Phone' })}
              </label>
              <Input
                type="text"
                value={contactPhone}
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                onChange={(e: any) => setContactPhone(e.target.value)}
                className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none text-nkz-sm bg-nkz-surface"
              />
            </div>
          </div>
        </div>

        {/* Limits */}
        <div className="border-t border-nkz-border pt-4">
          <h4 className="text-nkz-sm font-semibold text-nkz-text-primary mb-3">
            {t('admin.resource_limits', { defaultValue: 'Resource Limits' })}
          </h4>
          <p className="text-nkz-xs text-nkz-text-secondary mb-3">
            {t('admin.limits_hint', { defaultValue: 'Leave empty to use plan defaults (shown as placeholder).' })}
          </p>
          <div className="grid grid-cols-2 gap-4">
            {limitField(t('common.max_users'), maxUsers, setMaxUsers, defaults.maxUsers, 'maxUsers')}
            {limitField(t('common.max_robots'), maxRobots, setMaxRobots, defaults.maxRobots, 'maxRobots')}
            {limitField(t('common.max_sensors'), maxSensors, setMaxSensors, defaults.maxSensors, 'maxSensors')}
            {limitField(
              t('common.max_area_hectares'),
              maxAreaHectares,
              setMaxAreaHectares,
              defaults.maxAreaHectares,
              'maxAreaHectares'
            )}
          </div>
        </div>

        <div className="flex justify-end pt-4 border-t border-nkz-border">
          <Button
            onClick={handleSave}
            disabled={saving}
            className="px-6 py-2 bg-nkz-accent-base text-nkz-text-on-accent font-semibold rounded-nkz-lg hover:bg-nkz-accent-strong transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {saving ? t('common.saving') : t('common.save_changes')}
          </Button>
        </div>
      </div>
    </div>
  );
};
