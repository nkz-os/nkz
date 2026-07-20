// =============================================================================
// Tenant Users Management — consolidated with shared primitives (Settings page)
// =============================================================================

import React, { useState, useEffect } from 'react';
import { useI18n } from '@/context/I18nContext';
import api, { type TenantUser } from '@/services/api';
import { Users, Plus, X } from 'lucide-react';
import { UserTable, type UserRow } from '@/components/admin/UserTable';
import { UserEditModal } from '@/components/admin/UserEditModal';
import { useUserActions } from '@/components/admin/useUserActions';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';


/* eslint-disable @typescript-eslint/no-explicit-any */
interface TenantUsersManagementProps {
  canManageUsers: boolean;
}

const TENANT_ASSIGNABLE_ROLES = [
  { value: 'Farmer', label: 'Farmer', description: 'Basic dashboard access' },
  { value: 'TechnicalConsultant', label: 'Technical Consultant', description: 'Technical data and modules access' },
];

export const TenantUsersManagement: React.FC<TenantUsersManagementProps> = ({ canManageUsers }) => {
  const { t } = useI18n();

  const [tenantUsers, setTenantUsers] = useState<TenantUser[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [usersSuccess, setUsersSuccess] = useState<string | null>(null);
  const [showCreateUserModal, setShowCreateUserModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [editingUser, setEditingUser] = useState<UserRow | null>(null);
  const [planType, setPlanType] = useState<string | null>(null);
  const [maxUsersLimit, setMaxUsersLimit] = useState<number | null>(null);
  const [newUser, setNewUser] = useState({
    email: '',
    firstName: '',
    lastName: '',
    password: '',
    roles: ['Farmer'] as string[],
    temporary: true
  });

  const userActions = useUserActions({ apiBase: 'tenant' });

  useEffect(() => {
    if (canManageUsers) {
      loadTenantUsers();
      loadTenantProfile();
    }
  }, [canManageUsers]);

  const loadTenantProfile = async () => {
    try {
      const response = await api.get('/api/tenant/profile');
      const profile = response?.data || {};
      setPlanType(profile.plan_type || null);
      setMaxUsersLimit(profile.max_users === null ? null : Number(profile.max_users));
    } catch (err) {
      if (import.meta.env.DEV) logger.warn('Error loading tenant profile limits:', err);
    }
  };

  const loadTenantUsers = async () => {
    setLoadingUsers(true);
    setUsersError(null);
    try {
      const data = await api.getTenantUsers();
      setTenantUsers(data.users || []);
    } catch (err: unknown) {
      if (import.meta.env.DEV) logger.error('Error loading tenant users:', err);
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status !== 404) {
        setUsersError(t('settings.users.load_error'));
      }
    } finally {
      setLoadingUsers(false);
    }
  };

  const handleCreateUser = async () => {
    if (!newUser.email || !newUser.password || !newUser.firstName) {
      setUsersError(t('settings.users.required_fields'));
      setUsersSuccess(null);
      return;
    }

    setLoadingUsers(true);
    setUsersError(null);
    setUsersSuccess(null);
    try {
      await api.createTenantUser(newUser);
      setUsersSuccess(t('settings.users.created_success'));
      setShowCreateUserModal(false);
      setNewUser({
        email: '',
        firstName: '',
        lastName: '',
        password: '',
        roles: ['Farmer'],
        temporary: true
      });
      await loadTenantUsers();
      setTimeout(() => setUsersSuccess(null), 5000);
    } catch (err: any) {
      const payload = err?.response?.data || {};
      const displayError = payload?.message || payload?.message_en || payload?.error || err?.message;
      setUsersError(t('settings.users.create_error') + ': ' + displayError);
      setUsersSuccess(null);
    } finally {
      setLoadingUsers(false);
    }
  };

  const handleEditRoles = (user: UserRow) => {
    setEditingUser(user);
    setShowEditModal(true);
  };

  const handleSaveRoles = async (userId: string, data: { roles: string[]; firstName?: string; lastName?: string }) => {
    const ok = await userActions.updateRoles(userId, data.roles);
    if (ok) {
      setTenantUsers(tenantUsers.map(u => u.id === userId ? { ...u, roles: data.roles } : u));
      setShowEditModal(false);
      setEditingUser(null);
    }
  };

  const handleDeleteUser = async (userId: string, userEmail: string) => {
    const ok = await userActions.deleteUser(userId, userEmail);
    if (ok) await loadTenantUsers();
  };

  const handleResetPassword = async (userId: string) => {
    const password = await userActions.resetPassword(userId);
    if (password) {
      setUsersSuccess(t('settings.users.reset_success', { password }));
      setTimeout(() => setUsersSuccess(null), 10000);
    }
  };

  // Build UserRow list from TenantUser data
  const userRows: UserRow[] = tenantUsers.map(u => ({
    id: u.id,
    email: u.email,
    firstName: u.firstName,
    lastName: u.lastName,
    roles: u.roles || [],
    enabled: u.enabled !== false,
    createdAt: typeof (u as any).createdAt === 'number' ? (u as any).createdAt : undefined,
  }));

  const reachedUserLimit = maxUsersLimit !== null && tenantUsers.length >= maxUsersLimit;
  const limitLabel = maxUsersLimit === null ? '∞' : String(maxUsersLimit);

  if (!canManageUsers) return null;

  return (
    <>
      <div className="bg-white rounded-lg shadow-sm border border-nkz-border p-6">
        {(usersError || usersSuccess || userActions.error || userActions.success) && (
          <div className={`mb-4 p-3 rounded-lg ${(usersSuccess || userActions.success) ? 'bg-nkz-success-light border border-green-200' : 'bg-nkz-error-light border border-red-200'}`}>
            <p className={`text-sm ${(usersSuccess || userActions.success) ? 'text-green-800' : 'text-red-800'}`}>
              {usersSuccess || userActions.success || usersError || userActions.error}
            </p>
          </div>
        )}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
              <Users className="w-5 h-5 text-purple-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">{t('settings.users.title')}</h2>
              <p className="text-sm text-gray-600">{t('settings.users.subtitle')}</p>
              <p className="text-xs text-gray-500">
                {t('settings.users.usage_hint', { defaultValue: 'Users' })}: {tenantUsers.length} / {limitLabel}
                {planType ? ` (${planType})` : ''}
              </p>
            </div>
          </div>
          <Button
            onClick={() => setShowCreateUserModal(true)}
            disabled={reachedUserLimit}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
          >
            <Plus className="w-4 h-4" />
            {t('settings.users.new_user')}
          </Button>
        </div>
        {reachedUserLimit && (
          <p className="text-sm text-amber-700 mb-4">
            {t('settings.users.limit_reached', { defaultValue: 'You reached your plan user limit.' })}
          </p>
        )}

        <UserTable
          users={userRows}
          loading={loadingUsers}
          actions={{
            editRoles: true,
            resetPassword: true,
            deleteUser: true,
          }}
          onEditRoles={handleEditRoles}
          onResetPassword={handleResetPassword}
          onDeleteUser={handleDeleteUser}
          emptyMessage={t('settings.users.no_users')}
        />
      </div>

      {/* Create User Modal */}
      {showCreateUserModal && (
        <div className="fixed inset-0 bg-gray-600 bg-opacity-50 overflow-y-auto h-full w-full z-50">
          <div className="relative top-20 mx-auto p-5 border w-full max-w-2xl shadow-lg rounded-md bg-white">
            <div className="mt-3">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-medium text-gray-900">{t('settings.users.create_title')}</h3>
                <Button
                  onClick={() => setShowCreateUserModal(false)}
                  className="text-nkz-muted hover:text-gray-600"
                >
                  <X className="w-5 h-5" />
                </Button>
              </div>

              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    {t('settings.users.email')} *
                  </label>
                  <Input
                    type="email"
                    value={newUser.email}
                    onChange={(e: any) => setNewUser({ ...newUser, email: e.target.value })}
                    className="w-full px-3 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="user@example.com"
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      {t('settings.users.first_name')} *
                    </label>
                    <Input
                      type="text"
                      value={newUser.firstName}
                      onChange={(e: any) => setNewUser({ ...newUser, firstName: e.target.value })}
                      className="w-full px-3 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      {t('settings.users.last_name')}
                    </label>
                    <Input
                      type="text"
                      value={newUser.lastName}
                      onChange={(e: any) => setNewUser({ ...newUser, lastName: e.target.value })}
                      className="w-full px-3 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    {t('settings.users.temp_password')} *
                  </label>
                  <Input
                    type="password"
                    value={newUser.password}
                    onChange={(e: any) => setNewUser({ ...newUser, password: e.target.value })}
                    className="w-full px-3 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                  <p className="mt-1 text-xs text-nkz-muted">
                    {t('settings.users.temp_password_hint')}
                  </p>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {t('settings.users.roles')}
                  </label>
                  {TENANT_ASSIGNABLE_ROLES.map((role) => (
                    <label key={role.value} className="flex items-center mb-2">
                      <Input
                        type="checkbox"
                        checked={newUser.roles.includes(role.value)}
                        onChange={(e: any) => {
                          if (e.target.checked) {
                            setNewUser({ ...newUser, roles: [...newUser.roles, role.value] });
                          } else {
                            setNewUser({ ...newUser, roles: newUser.roles.filter(r => r !== role.value) });
                          }
                        }}
                        className="mr-2"
                      />
                      <span className="text-sm">{role.label} - {role.description}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="flex justify-end space-x-3 mt-6">
                <Button
                  onClick={() => setShowCreateUserModal(false)}
                  className="px-4 py-2 text-sm font-medium text-gray-700 bg-nkz-bg-secondary rounded-lg hover:bg-gray-200"
                >
                  {t('settings.cancel')}
                </Button>
                <Button
                  onClick={handleCreateUser}
                  disabled={loadingUsers || !newUser.email || !newUser.password || !newUser.firstName}
                  className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {loadingUsers ? t('settings.users.creating') : t('settings.users.create_button')}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Edit Roles Modal (Tenant context — limited roles) */}
      {showEditModal && editingUser && (
        <UserEditModal
          user={editingUser}
          availableRoles={TENANT_ASSIGNABLE_ROLES}
          isPlatformContext={false}
          loading={userActions.loading}
          onSave={handleSaveRoles}
          onClose={() => {
            setShowEditModal(false);
            setEditingUser(null);
            userActions.clearMessages();
          }}
        />
      )}
    </>
  );
};
