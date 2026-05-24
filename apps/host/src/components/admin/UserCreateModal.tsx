import React, { useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import client from '@/services/api';

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
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-gray-900">
            {t('admin.create_user_title', { defaultValue: 'Create New User' })}
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="h-5 w-5" />
          </button>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800">
            {error}
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('settings.users.email')} *
            </label>
            <input
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent outline-none"
              placeholder="user@example.com"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('settings.users.first_name')} *
              </label>
              <input
                type="text"
                value={form.firstName}
                onChange={(e) => setForm({ ...form, firstName: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('settings.users.last_name')}
              </label>
              <input
                type="text"
                value={form.lastName}
                onChange={(e) => setForm({ ...form, lastName: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent outline-none"
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('settings.users.temp_password')} *
            </label>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              {t('settings.users.roles')}
            </label>
            <div className="space-y-2 max-h-40 overflow-y-auto">
              {ASSIGNABLE_ROLES.map((role) => (
                <label key={role.value} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={form.roles.includes(role.value)}
                    onChange={() => toggleRole(role.value)}
                    className="rounded"
                  />
                  <span className="text-sm">{role.label}</span>
                </label>
              ))}
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || !form.email || !form.firstName || !form.password}
            className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            {t('admin.create_user_button', { defaultValue: 'Create User' })}
          </button>
        </div>
      </div>
    </div>
  );
};
