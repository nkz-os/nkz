// =============================================================================
// Shared UserTable — avatar, name, email, roles badges, status, actions
// =============================================================================
import React from 'react';
import { Mail, Trash2, Edit2, Key } from 'lucide-react';
import { format } from 'date-fns';
import { Button } from '@nekazari/ui-kit';

export interface UserRow {
  id: string;
  email: string;
  username?: string;
  firstName?: string;
  lastName?: string;
  roles: string[];
  enabled: boolean;
  tenant?: string;
  createdAt?: number;
  groups?: string[];
}

interface UserTableProps {
  users: UserRow[];
  loading: boolean;
  /** Actions available in this context */
  actions: {
    editRoles?: boolean;
    resetPassword?: boolean;
    deleteUser?: boolean;
  };
  onEditRoles?: (user: UserRow) => void;
  onResetPassword?: (userId: string) => void;
  onDeleteUser?: (userId: string, email: string) => void;
  emptyMessage?: string;
}

function roleBadgeClass(role: string): string {
  const base = 'text-[10px] px-1.5 py-0.5 rounded font-medium';
  switch (role) {
    case 'PlatformAdmin':
      return `${base} bg-nkz-info-soft text-nkz-info-strong`;
    case 'TenantAdmin':
      return `${base} bg-nkz-info-soft text-nkz-info-strong`;
    case 'TechnicalConsultant':
    case 'DeviceManager':
      return `${base} bg-nkz-accent-soft text-nkz-accent-strong`;
    case 'Farmer':
      return `${base} bg-nkz-warning-soft text-nkz-warning-strong`;
    case 'GestorCUE':
      return `${base} bg-nkz-danger-soft text-nkz-danger-strong`;
    case 'role_pro_expired':
      return `${base} bg-nkz-danger-soft text-nkz-danger-strong`;
    default:
      return `${base} bg-nkz-surface-sunken text-nkz-text-secondary`;
  }
}

export const UserTable: React.FC<UserTableProps> = ({
  users,
  loading,
  actions,
  onEditRoles,
  onResetPassword,
  onDeleteUser,
  emptyMessage = 'No users found.',
}) => {
  if (loading && users.length === 0) {
    return (
      <div className="p-12 text-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-nkz-accent-base mx-auto mb-4" />
        <p className="text-nkz-text-secondary">Loading users...</p>
      </div>
    );
  }

  if (users.length === 0) {
    return (
      <div className="p-12 text-center bg-nkz-surface-sunken">
        <p className="text-nkz-text-secondary font-medium">{emptyMessage}</p>
      </div>
    );
  }

  const hasActions = actions.editRoles || actions.resetPassword || actions.deleteUser;

  return (
    <table className="w-full text-left">
      <thead className="bg-nkz-surface-sunken border-b border-nkz-border">
        <tr>
          <th className="px-6 py-4 font-semibold text-nkz-text-primary text-nkz-sm">User</th>
          <th className="px-6 py-4 font-semibold text-nkz-text-primary text-nkz-sm">Tenant</th>
          <th className="px-6 py-4 font-semibold text-nkz-text-primary text-nkz-sm">Roles</th>
          <th className="px-6 py-4 font-semibold text-nkz-text-primary text-nkz-sm">Status</th>
          <th className="px-6 py-4 font-semibold text-nkz-text-primary text-nkz-sm">Registered</th>
          {hasActions && (
            <th className="px-6 py-4 font-semibold text-nkz-text-primary text-nkz-sm text-right">Actions</th>
          )}
        </tr>
      </thead>
      <tbody className="divide-y divide-nkz-border">
        {users.map((user) => (
          <tr key={user.id} className="hover:bg-nkz-canvas transition-colors">
            <td className="px-6 py-4">
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-full bg-nkz-accent-soft flex items-center justify-center text-nkz-accent-strong font-bold text-nkz-sm">
                  {(user.firstName?.[0] || user.email[0] || '?').toUpperCase()}
                </div>
                <div>
                  <p className="font-semibold text-nkz-text-primary">
                    {[user.firstName, user.lastName].filter(Boolean).join(' ') || user.email}
                  </p>
                  <p className="text-nkz-xs text-nkz-text-secondary flex items-center gap-1">
                    <Mail className="h-3 w-3" /> {user.email}
                  </p>
                  {user.id && (
                    <p className="text-[10px] text-nkz-text-muted font-mono" title="Keycloak user id">
                      KC: {user.id}
                    </p>
                  )}
                </div>
              </div>
            </td>
            <td className="px-6 py-4">
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-nkz-xs font-medium bg-nkz-info-soft text-nkz-info-strong">
                {user.tenant || 'no-tenant'}
              </span>
            </td>
            <td className="px-6 py-4">
              <div className="flex flex-wrap gap-1">
                {(user.roles || []).map((role) => (
                  <span key={role} className={roleBadgeClass(role)}>
                    {role}
                  </span>
                ))}
              </div>
            </td>
            <td className="px-6 py-4 text-nkz-sm">
              <div className={`flex items-center gap-1.5 font-medium ${user.enabled ? 'text-nkz-success' : 'text-nkz-danger'}`}>
                <div className={`h-2 w-2 rounded-full ${user.enabled ? 'bg-nkz-success' : 'bg-nkz-danger'}`} />
                {user.enabled ? 'Active' : 'Disabled'}
              </div>
            </td>
            <td className="px-6 py-4 text-nkz-sm text-nkz-text-secondary">
              {user.createdAt != null
                ? format(new Date(user.createdAt), 'dd/MM/yyyy')
                : 'N/A'}
            </td>
            {hasActions && (
              <td className="px-6 py-4 text-right">
                <div className="flex justify-end gap-2">
                  {actions.editRoles && onEditRoles && (
                    <Button
                      onClick={() => onEditRoles(user)}
                      className="p-2 text-nkz-text-muted hover:text-nkz-info transition-colors"
                      title="Edit roles"
                    >
                      <Edit2 className="h-4 w-4" />
                    </Button>
                  )}
                  {actions.resetPassword && onResetPassword && (
                    <Button
                      onClick={() => onResetPassword(user.id)}
                      className="p-2 text-nkz-text-muted hover:text-nkz-warning transition-colors"
                      title="Reset password"
                    >
                      <Key className="h-4 w-4" />
                    </Button>
                  )}
                  {actions.deleteUser && onDeleteUser && (
                    <Button
                      onClick={() => onDeleteUser(user.id, user.email)}
                      className="p-2 text-nkz-text-muted hover:text-nkz-danger transition-colors"
                      title="Delete user"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
};
