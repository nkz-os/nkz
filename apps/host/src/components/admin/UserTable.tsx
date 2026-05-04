// =============================================================================
// Shared UserTable — avatar, name, email, roles badges, status, actions
// =============================================================================
import React from 'react';
import { Mail, Trash2, Edit2, Key } from 'lucide-react';
import { format } from 'date-fns';

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
      return `${base} bg-purple-100 text-purple-800`;
    case 'TenantAdmin':
      return `${base} bg-blue-100 text-blue-800`;
    case 'TechnicalConsultant':
    case 'DeviceManager':
      return `${base} bg-green-100 text-green-800`;
    case 'Farmer':
      return `${base} bg-yellow-100 text-yellow-800`;
    case 'GestorCUE':
      return `${base} bg-rose-100 text-rose-800`;
    case 'role_pro_expired':
      return `${base} bg-red-100 text-red-800`;
    default:
      return `${base} bg-gray-100 text-gray-600`;
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
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-green-600 mx-auto mb-4" />
        <p className="text-gray-500">Loading users...</p>
      </div>
    );
  }

  if (users.length === 0) {
    return (
      <div className="p-12 text-center bg-gray-50">
        <p className="text-gray-500 font-medium">{emptyMessage}</p>
      </div>
    );
  }

  const hasActions = actions.editRoles || actions.resetPassword || actions.deleteUser;

  return (
    <table className="w-full text-left">
      <thead className="bg-gray-50 border-b border-gray-100">
        <tr>
          <th className="px-6 py-4 font-semibold text-gray-700 text-sm">User</th>
          <th className="px-6 py-4 font-semibold text-gray-700 text-sm">Tenant</th>
          <th className="px-6 py-4 font-semibold text-gray-700 text-sm">Roles</th>
          <th className="px-6 py-4 font-semibold text-gray-700 text-sm">Status</th>
          <th className="px-6 py-4 font-semibold text-gray-700 text-sm">Registered</th>
          {hasActions && (
            <th className="px-6 py-4 font-semibold text-gray-700 text-sm text-right">Actions</th>
          )}
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-100">
        {users.map((user) => (
          <tr key={user.id} className="hover:bg-gray-50 transition-colors">
            <td className="px-6 py-4">
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-full bg-green-100 flex items-center justify-center text-green-700 font-bold text-sm">
                  {(user.firstName?.[0] || user.email[0] || '?').toUpperCase()}
                </div>
                <div>
                  <p className="font-semibold text-gray-900">
                    {[user.firstName, user.lastName].filter(Boolean).join(' ') || user.email}
                  </p>
                  <p className="text-xs text-gray-500 flex items-center gap-1">
                    <Mail className="h-3 w-3" /> {user.email}
                  </p>
                  {user.id && (
                    <p className="text-[10px] text-gray-400 font-mono" title="Keycloak user id">
                      KC: {user.id}
                    </p>
                  )}
                </div>
              </div>
            </td>
            <td className="px-6 py-4">
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
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
            <td className="px-6 py-4 text-sm">
              <div className={`flex items-center gap-1.5 font-medium ${user.enabled ? 'text-green-600' : 'text-red-600'}`}>
                <div className={`h-2 w-2 rounded-full ${user.enabled ? 'bg-green-600' : 'bg-red-600'}`} />
                {user.enabled ? 'Active' : 'Disabled'}
              </div>
            </td>
            <td className="px-6 py-4 text-sm text-gray-500">
              {user.createdAt != null
                ? format(new Date(user.createdAt), 'dd/MM/yyyy')
                : 'N/A'}
            </td>
            {hasActions && (
              <td className="px-6 py-4 text-right">
                <div className="flex justify-end gap-2">
                  {actions.editRoles && onEditRoles && (
                    <button
                      onClick={() => onEditRoles(user)}
                      className="p-2 text-gray-400 hover:text-blue-600 transition-colors"
                      title="Edit roles"
                    >
                      <Edit2 className="h-4 w-4" />
                    </button>
                  )}
                  {actions.resetPassword && onResetPassword && (
                    <button
                      onClick={() => onResetPassword(user.id)}
                      className="p-2 text-gray-400 hover:text-yellow-600 transition-colors"
                      title="Reset password"
                    >
                      <Key className="h-4 w-4" />
                    </button>
                  )}
                  {actions.deleteUser && onDeleteUser && (
                    <button
                      onClick={() => onDeleteUser(user.id, user.email)}
                      className="p-2 text-gray-400 hover:text-red-600 transition-colors"
                      title="Delete user"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
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
