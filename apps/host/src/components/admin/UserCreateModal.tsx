import React, { useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import client from '@/services/api';
import { Button, Input } from '@nekazari/ui-kit';

interface UserCreateModalProps {
  tenantId: string;
  onClose: () => void;
  onCreated: () => void;
}

const ASSIGNABLE_ROLES = [
  { value: 'PlatformAdmin', label: 'Platform Admin' },
  { value: 'TenantAdmin', label: 'Tenant Admin' },
  { value: 'GestorCUE', label: 'Gestor CUE' },
  { value: 'TechnicalConsultant', label: 'Technical Consultant' },
  { value: 'Farmer', label: 'Farmer' },
];

export const UserCreateModal: React.FC<UserCreateModalProps> = ({
  tenantId,
  onClose,
  onCreated,
}) => {
  const { t } = useI18n();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    email: '',
    firstName: '',
    lastName: '',
    password: '',
    roles: ['Farmer'] as string[],
  });

  const toggleRole = (role: string) => {
    setForm((f) => ({
      ...f,
      roles: f.roles.includes(role)
        ? f.roles.filter((r) => r !== role)
        : [...f.roles, role],
    }));
  };

  const handleSubmit = async () => {
    if (!form.email || !form.firstName || !form.password) {
      setError(t('settings.users.required_fields'));
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await client.post(`/api/admin/tenants/${tenantId}/users`, {
        email: form.email,
        firstName: form.firstName,
        lastName: form.lastName,
        password: form.password,
        roles: form.roles,
      });
      onCreated();
    } catch (err: any) {
      setError(
        err?.response?.data?.error || err.message || t('settings.users.create_error')
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-nkz-modal" onClick={onClose}>
      <div className="bg-nkz-surface-raised rounded-nkz-xl shadow-nkz-xl p-6 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-nkz-lg font-semibold text-nkz-text-primary">
            {t('admin.create_user_title', { defaultValue: 'Create New User' })}
          </h3>
          <Button onClick={onClose} className="text-nkz-text-muted hover:text-nkz-text-secondary">
            <X className="h-5 w-5" />
          </Button>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-nkz-danger-soft border border-nkz-danger-soft rounded-nkz-lg text-nkz-sm text-nkz-danger-strong">
            {error}
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
              {t('settings.users.email')} *
            </label>
            <Input
              type="email"
              value={form.email}
              onChange={(e: any) => setForm({ ...form, email: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
              placeholder="user@example.com"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
                {t('settings.users.first_name')} *
              </label>
              <Input
                type="text"
                value={form.firstName}
                onChange={(e: any) => setForm({ ...form, firstName: e.target.value })}
                className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
              />
            </div>
            <div>
              <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
                {t('settings.users.last_name')}
              </label>
              <Input
                type="text"
                value={form.lastName}
                onChange={(e: any) => setForm({ ...form, lastName: e.target.value })}
                className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
              />
            </div>
          </div>
          <div>
            <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
              {t('settings.users.temp_password')} *
            </label>
            <Input
              type="password"
              value={form.password}
              onChange={(e: any) => setForm({ ...form, password: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
            />
          </div>
          <div>
            <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-2">
              {t('settings.users.roles')}
            </label>
            <div className="space-y-2 max-h-40 overflow-y-auto">
              {ASSIGNABLE_ROLES.map((role) => (
                <label key={role.value} className="flex items-center gap-2 cursor-pointer">
                  <Input
                    type="checkbox"
                    checked={form.roles.includes(role.value)}
                    onChange={() => toggleRole(role.value)}
                    className="rounded"
                  />
                  <span className="text-nkz-sm text-nkz-text-primary">{role.label}</span>
                </label>
              ))}
            </div>
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
            disabled={loading || !form.email || !form.firstName || !form.password}
            className="px-4 py-2 bg-nkz-accent-base text-nkz-text-on-accent rounded-nkz-lg hover:bg-nkz-accent-strong transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            {t('admin.create_user_button', { defaultValue: 'Create User' })}
          </Button>
        </div>
      </div>
    </div>
  );
};
