import React, { useMemo, useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import client from '@/services/api';
import { Button, Input } from '@nekazari/ui-kit';
import {

  normalizeTenantId,
  validateTenantId,
  MIN_TENANT_ID_LENGTH,
  MAX_TENANT_ID_LENGTH,
} from '@/utils/tenantValidation';

interface TenantCreateModalProps {
  onClose: () => void;
  onCreated: () => void;
}

interface BackendError {
  message: string;
  errorCode?: string;
  requestId?: string;
}

export const TenantCreateModal: React.FC<TenantCreateModalProps> = ({
  onClose,
  onCreated,
}) => {
  const { t } = useI18n();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<BackendError | null>(null);
  const [form, setForm] = useState({
    tenant_name: '',
    email: '',
    plan: 'premium',
    password: '',
  });

  const preview = useMemo(() => {
    if (!form.tenant_name) return '';
    return normalizeTenantId(form.tenant_name);
  }, [form.tenant_name]);

  const previewValidation = useMemo(
    () => (form.tenant_name ? validateTenantId(form.tenant_name) : null),
    [form.tenant_name],
  );
  const previewValid = previewValidation?.isValid ?? false;

  const handleSubmit = async () => {
    setError(null);
    if (!form.tenant_name || !form.email) {
      setError({
        message: t('admin.create_tenant_required', {
          defaultValue: 'Name and email are required.',
        }),
      });
      return;
    }
    if (!previewValid) {
      setError({
        message: t('admin.create_tenant_invalid_name', {
          defaultValue:
            'Tenant name must produce a valid id (lowercase letters, digits, hyphens; 3-47 chars).',
        }),
        errorCode: 'INVALID_TENANT_NAME',
      });
      return;
    }
    setLoading(true);
    try {
      // Tenant creation runs an 11-step K8s provisioning script
      // (create-tenant.sh: namespace, NetworkPolicy, ResourceQuota, RBAC,
      // ServiceAccount, secrets, optional DB provision job, kubectl
      // waits) which routinely takes 60-180 s and can spike to ~5 min
      // when the per-tenant DB provision job hits its backoff limit.
      // Override the 30s default so the UI follows the backend.
      // Matches the api-gateway proxy timeout for the same endpoint.
      await client.post(
        '/api/admin/tenants',
        {
          tenant_name: form.tenant_name,
          email: form.email,
          plan: form.plan,
          password: form.password || undefined,
        },
        { timeout: 300000 },
      );
      onCreated();
    } catch (err: any) {
      const data = err?.response?.data ?? {};
      setError({
        message:
          data.error ||
          err.message ||
          t('admin.create_tenant_error', { defaultValue: 'Failed to create tenant.' }),
        errorCode: data.error_code,
        requestId: data.request_id,
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-nkz-modal"
      onClick={onClose}
    >
      <div
        className="bg-nkz-surface-raised rounded-nkz-xl shadow-nkz-xl p-6 w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-nkz-lg font-semibold text-nkz-text-primary">
            {t('admin.create_tenant_title', { defaultValue: 'Create Tenant' })}
          </h3>
          <Button
            onClick={onClose}
            className="text-nkz-text-muted hover:text-nkz-text-secondary"
          >
            <X className="h-5 w-5" />
          </Button>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-nkz-danger-soft border border-nkz-danger-soft rounded-nkz-lg text-nkz-sm text-nkz-danger-strong">
            <div>{error.message}</div>
            {error.errorCode && (
              <div className="text-nkz-xs opacity-75 mt-1">
                {t('admin.error_code', { defaultValue: 'code' })}: {error.errorCode}
              </div>
            )}
            {error.requestId && (
              <div className="text-nkz-xs opacity-75">
                {t('admin.error_request_id', { defaultValue: 'Reference' })}:{' '}
                {error.requestId}
              </div>
            )}
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
              {t('admin.tenant_name', { defaultValue: 'Tenant Name' })} *
            </label>
            <Input
              type="text"
              value={form.tenant_name}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              onChange={(e: any) => setForm({ ...form, tenant_name: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
              placeholder="My Farm"
              maxLength={64}
            />
            {form.tenant_name && (
              <p className="mt-1 text-nkz-xs text-nkz-text-muted">
                {t('admin.tenant_id_preview', { defaultValue: 'ID will be:' })}{' '}
                <code
                  className={
                    previewValid
                      ? 'text-nkz-text-secondary'
                      : 'text-nkz-danger-strong'
                  }
                >
                  {preview || '—'}
                </code>
              </p>
            )}
            <p className="mt-1 text-nkz-xs text-nkz-text-muted">
              {t('admin.tenant_id_rule', {
                defaultValue: `Lowercase letters, digits and hyphens, ${MIN_TENANT_ID_LENGTH}-${MAX_TENANT_ID_LENGTH} chars.`,
                min: MIN_TENANT_ID_LENGTH,
                max: MAX_TENANT_ID_LENGTH,
              })}
            </p>
          </div>
          <div>
            <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
              {t('admin.owner_email', { defaultValue: 'Owner Email' })} *
            </label>
            <Input
              type="email"
              value={form.email}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              onChange={(e: any) => setForm({ ...form, email: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
              placeholder="owner@example.com"
            />
          </div>
          <div>
            <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
              {t('admin.plan', { defaultValue: 'Plan' })}
            </label>
            <select
              value={form.plan}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              onChange={(e: any) => setForm({ ...form, plan: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
            >
              <option value="basic">{t('common.basic')}</option>
              <option value="premium">{t('common.premium')}</option>
              <option value="pro">{t('settings.tier.pro', { defaultValue: 'Pro' })}</option>
              <option value="enterprise">{t('common.enterprise')}</option>
            </select>
          </div>
          <div>
            <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
              {t('admin.owner_password', {
                defaultValue: 'Owner Password (optional, auto-generated if empty)',
              })}
            </label>
            <Input
              type="password"
              value={form.password}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              onChange={(e: any) => setForm({ ...form, password: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
              placeholder="Leave empty to auto-generate"
            />
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <Button
            onClick={onClose}
            className="px-4 py-2 text-nkz-text-secondary bg-nkz-surface-sunken rounded-nkz-lg hover:bg-nkz-border transition-colors"
          >
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={loading || !form.tenant_name || !form.email || !previewValid}
            className="px-4 py-2 bg-nkz-accent-base text-nkz-text-on-accent rounded-nkz-lg hover:bg-nkz-accent-strong transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            {t('admin.create_tenant_button', { defaultValue: 'Create Tenant' })}
          </Button>
        </div>
      </div>
    </div>
  );
};
