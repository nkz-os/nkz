// =============================================================================
// Shared user action hooks — used by both PlatformAdmin and TenantAdmin views
// =============================================================================
import { useState } from 'react';
import api from '@/services/api';

interface UserActionsOptions {
  /** Which API base to use: /api/admin for PlatformAdmin, /api/tenant for TenantAdmin */
  apiBase: 'admin' | 'tenant';
}

export interface UserActions {
  loading: boolean;
  error: string | null;
  success: string | null;
  deleteUser: (userId: string, userEmail: string) => Promise<boolean>;
  resetPassword: (userId: string) => Promise<string | null>;
  updateRoles: (userId: string, roles: string[]) => Promise<boolean>;
  clearMessages: () => void;
}

export function useUserActions({ apiBase }: UserActionsOptions): UserActions {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const clearMessages = () => {
    setError(null);
    setSuccess(null);
  };

  const deleteUser = async (userId: string, userEmail: string): Promise<boolean> => {
    const confirmed = window.confirm(
      `Are you sure you want to delete user ${userEmail}? This action cannot be undone.`
    );
    if (!confirmed) return false;

    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      await api.delete(`/api/${apiBase}/users/${userId}`);
      setSuccess(`User ${userEmail} deleted successfully.`);
      return true;
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error
        || (err as Error).message
        || 'Failed to delete user';
      setError(msg);
      return false;
    } finally {
      setLoading(false);
    }
  };

  const resetPassword = async (userId: string): Promise<string | null> => {
    const confirmed = window.confirm('Are you sure you want to reset this user\'s password?');
    if (!confirmed) return null;

    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const response = await api.post(`/api/${apiBase}/users/${userId}/reset-password`);
      const password = response.data?.temporaryPassword || response.data?.data?.temporaryPassword || 'N/A';
      setSuccess(`Password reset. Temporary password: ${password}`);
      return password;
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error
        || (err as Error).message
        || 'Failed to reset password';
      setError(msg);
      return null;
    } finally {
      setLoading(false);
    }
  };

  const updateRoles = async (userId: string, roles: string[]): Promise<boolean> => {
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      await api.put(`/api/${apiBase}/users/${userId}/roles`, { roles });
      setSuccess('Roles updated successfully.');
      return true;
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error
        || (err as Error).message
        || 'Failed to update roles';
      setError(msg);
      return false;
    } finally {
      setLoading(false);
    }
  };

  return {
    loading,
    error,
    success,
    deleteUser,
    resetPassword,
    updateRoles,
    clearMessages,
  };
}
