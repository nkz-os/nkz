// =============================================================================
// Shared UserEditModal — edit user roles (and name for tenant context)
// =============================================================================
import React, { useState, useEffect } from 'react';
import { X, Mail, AlertTriangle } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import type { UserRow } from './UserTable';

interface UserEditModalProps {
  user: UserRow;
  /** Roles available for assignment in this context */
  availableRoles: { value: string; label: string; description?: string }[];
  /** Whether name editing is allowed (tenant context) */
  allowNameEdit?: boolean;
  /** Whether this is a PlatformAdmin context (allows all roles) */
  isPlatformContext?: boolean;
  loading: boolean;
  onSave: (userId: string, data: { firstName?: string; lastName?: string; roles: string[] }) => void;
  onClose: () => void;
}

export const UserEditModal: React.FC<UserEditModalProps> = ({
  user,
  availableRoles,
  allowNameEdit = false,
  isPlatformContext = false,
  loading,
  onSave,
  onClose,
}) => {
  const { t } = useI18n();
  const [firstName, setFirstName] = useState(user.firstName || '');
  const [lastName, setLastName] = useState(user.lastName || '');
  const [selectedRoles, setSelectedRoles] = useState<string[]>([...user.roles]);

  useEffect(() => {
    setFirstName(user.firstName || '');
    setLastName(user.lastName || '');
    setSelectedRoles([...user.roles]);
  }, [user]);

  const toggleRole = (role: string) => {
    setSelectedRoles((prev) =>
      prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]
    );
  };

  const handleSave = () => {
    const data: { firstName?: string; lastName?: string; roles: string[] } = {
      roles: selectedRoles,
    };
    if (allowNameEdit) {
      data.firstName = firstName;
      data.lastName = lastName;
    }
    onSave(user.id, data);
  };

  return (
    <div className="fixed inset-0 bg-gray-600 bg-opacity-50 overflow-y-auto h-full w-full z-50">
      <div className="relative top-20 mx-auto p-5 border w-full max-w-lg shadow-lg rounded-md bg-white">
        <div className="mt-3">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-medium text-gray-900">
              {t('settings.users.edit_title')}: {[user.firstName, user.lastName].filter(Boolean).join(' ') || user.email}
            </h3>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="space-y-4">
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <Mail className="w-4 h-4" />
              {user.email}
            </div>

            {allowNameEdit && (
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    {t('settings.users.first_name')}
                  </label>
                  <input
                    type="text"
                    value={firstName}
                    onChange={(e) => setFirstName(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    {t('settings.users.last_name')}
                  </label>
                  <input
                    type="text"
                    value={lastName}
                    onChange={(e) => setLastName(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                </div>
              </div>
            )}

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                {t('settings.users.roles')}
              </label>
              {availableRoles.map((role) => (
                <label key={role.value} className="flex items-center mb-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedRoles.includes(role.value)}
                    onChange={() => toggleRole(role.value)}
                    className="mr-2"
                  />
                  <span className="text-sm">
                    {role.label}
                    {role.description && (
                      <span className="text-gray-500"> - {role.description}</span>
                    )}
                  </span>
                </label>
              ))}

              {!isPlatformContext && (
                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mt-3">
                  <p className="text-xs text-yellow-800">
                    <AlertTriangle className="w-3 h-3 inline mr-1" />
                    {t('settings.users.platform_only_roles_warning', {
                      defaultValue: 'Only PlatformAdmin can assign PlatformAdmin, TenantAdmin, and GestorCUE roles.',
                    })}
                  </p>
                </div>
              )}
            </div>
          </div>

          <div className="flex justify-end space-x-3 mt-6">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200"
            >
              {t('settings.cancel')}
            </button>
            <button
              onClick={handleSave}
              disabled={loading}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? t('settings.saving') : t('settings.save_changes')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
