import React, { useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import client from '@/services/api';

interface TenantCreateModalProps {
  onClose: () => void;
  onCreated: () => void;
}

export const TenantCreateModal: React.FC<TenantCreateModalProps> = ({
  onClose,
  onCreated,
}) => {
  const { t } = useI18n();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    tenant_name: '',
    email: '',
    plan: 'premium',
    password: '',
  });

  const handleSubmit = async () => {
    if (!form.tenant_name || !form.email) {
      setError(t('admin.create_tenant_required', { defaultValue: 'Name and email are required.' }));
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await client.post('/api/admin/tenants', {
        tenant_name: form.tenant_name,
        email: form.email,
        plan: form.plan,
        password: form.password || undefined,
      });
      onCreated();
    } catch (err: any) {
      setError(
        err?.response?.data?.error || err.message || t('admin.create_tenant_error', { defaultValue: 'Failed to create tenant.' })
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-nkz-modal" onClick={onClose}>
      <div className="bg-nkz-surface-raised rounded-nkz-xl shadow-nkz-xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-nkz-lg font-semibold text-nkz-text-primary">
            {t('admin.create_tenant_title', { defaultValue: 'Create Tenant' })}
          </h3>
          <button onClick={onClose} className="text-nkz-text-muted hover:text-nkz-text-secondary">
            <X className="h-5 w-5" />
          </button>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-nkz-danger-soft border border-nkz-danger-soft rounded-nkz-lg text-nkz-sm text-nkz-danger-strong">
            {error}
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
              {t('admin.tenant_name', { defaultValue: 'Tenant Name' })} *
            </label>
            <input
              type="text"
              value={form.tenant_name}
              onChange={(e) => setForm({ ...form, tenant_name: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
              placeholder="My Farm"
            />
          </div>
          <div>
            <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
              {t('admin.owner_email', { defaultValue: 'Owner Email' })} *
            </label>
            <input
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
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
              onChange={(e) => setForm({ ...form, plan: e.target.value })}
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
              {t('admin.owner_password', { defaultValue: 'Owner Password (optional, auto-generated if empty)' })}
            </label>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
              placeholder="Leave empty to auto-generate"
            />
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 text-nkz-text-secondary bg-nkz-surface-sunken rounded-nkz-lg hover:bg-nkz-border transition-colors"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || !form.tenant_name || !form.email}
            className="px-4 py-2 bg-nkz-accent-base text-nkz-text-on-accent rounded-nkz-lg hover:bg-nkz-accent-strong transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            {t('admin.create_tenant_button', { defaultValue: 'Create Tenant' })}
          </button>
        </div>
      </div>
    </div>
  );
};
