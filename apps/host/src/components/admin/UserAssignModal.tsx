import React, { useState, useEffect } from 'react';
import { X, Search, Loader2, Mail } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import client from '@/services/api';

interface UserResult {
  id: string;
  email: string;
  firstName: string;
  lastName: string;
  username: string;
  tenant?: string;
}

interface UserAssignModalProps {
  tenantId: string;
  onClose: () => void;
  onAssigned: () => void;
}

const ASSIGNABLE_ROLES = [
  { value: 'Farmer', label: 'Farmer' },
  { value: 'TechnicalConsultant', label: 'Technical Consultant' },
  { value: 'TenantAdmin', label: 'Tenant Admin' },
  { value: 'GestorCUE', label: 'Gestor CUE' },
];

export const UserAssignModal: React.FC<UserAssignModalProps> = ({
  tenantId,
  onClose,
  onAssigned,
}) => {
  const { t } = useI18n();
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [results, setResults] = useState<UserResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [selectedUser, setSelectedUser] = useState<UserResult | null>(null);
  const [role, setRole] = useState('Farmer');
  const [assigning, setAssigning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Debounce search
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  // Search users
  useEffect(() => {
    if (!debouncedSearch || debouncedSearch.length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    (async () => {
      setSearching(true);
      try {
        const resp = await client.get(
          `/api/admin/users?search=${encodeURIComponent(debouncedSearch)}&max=20`
        );
        if (cancelled) return;
        const raw = (resp.data?.users || []) as Record<string, unknown>[];
        setResults(
          raw.map((u) => ({
            id: String(u.id ?? ''),
            email: String(u.email ?? ''),
            firstName: String(u.firstName ?? ''),
            lastName: String(u.lastName ?? ''),
            username: String(u.username ?? ''),
            tenant: typeof u.tenant_id === 'string' ? u.tenant_id : undefined,
          }))
        );
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    })();
    return () => { cancelled = true; };
  }, [debouncedSearch]);

  const handleAssign = async () => {
    if (!selectedUser) return;
    setAssigning(true);
    setError(null);
    try {
      await client.post(`/api/admin/tenants/${tenantId}/users`, {
        email: selectedUser.email,
        role,
      });
      setSuccessMsg(
        t('admin.user_assigned', { defaultValue: 'User assigned to tenant.' })
      );
      setTimeout(() => {
        onAssigned();
      }, 800);
    } catch (err: any) {
      setError(
        err?.response?.data?.error || err.message || 'Failed to assign user'
      );
    } finally {
      setAssigning(false);
    }
  };

  if (successMsg && selectedUser) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
        <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md text-center" onClick={(e) => e.stopPropagation()}>
          <div className="text-green-600 text-lg font-semibold mb-2">{successMsg}</div>
          <p className="text-sm text-gray-500">
            {selectedUser.email} &rarr; {role}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-gray-900">
            {t('admin.assign_user_title', { defaultValue: 'Assign Existing User' })}
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

        {/* Search */}
        {!selectedUser && (
          <>
            <div className="relative mb-4">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
              <input
                type="text"
                placeholder={t('admin.search_users_placeholder', { defaultValue: 'Search by name or email...' })}
                className="w-full pl-9 pr-4 py-2 border border-gray-200 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent outline-none text-sm"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                autoFocus
              />
            </div>

            {/* Results */}
            <div className="max-h-60 overflow-y-auto border border-gray-100 rounded-lg">
              {searching && (
                <div className="p-4 text-center text-sm text-gray-500">
                  {t('common.loading')}
                </div>
              )}
              {!searching && debouncedSearch.length < 2 && (
                <div className="p-4 text-center text-sm text-gray-400">
                  {t('admin.type_to_search', { defaultValue: 'Type at least 2 characters to search' })}
                </div>
              )}
              {!searching && debouncedSearch.length >= 2 && results.length === 0 && (
                <div className="p-4 text-center text-sm text-gray-500">
                  {t('admin.no_user_results', { defaultValue: 'No users found.' })}
                </div>
              )}
              {results.map((user) => (
                <button
                  key={user.id}
                  onClick={() => setSelectedUser(user)}
                  className="w-full text-left p-3 hover:bg-gray-50 border-b border-gray-100 last:border-b-0 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <div className="h-8 w-8 rounded-full bg-green-100 flex items-center justify-center text-green-700 font-bold text-xs shrink-0">
                      {(user.firstName?.[0] || user.email[0] || '?').toUpperCase()}
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-gray-900 truncate">
                        {[user.firstName, user.lastName].filter(Boolean).join(' ') || user.email}
                      </p>
                      <p className="text-xs text-gray-500 flex items-center gap-1">
                        <Mail className="h-3 w-3" /> {user.email}
                      </p>
                      {user.tenant && (
                        <span className="text-[10px] text-gray-400">
                          {t('admin.current_tenant', { defaultValue: 'Current tenant' })}: {user.tenant}
                        </span>
                      )}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </>
        )}

        {/* Selected user + role */}
        {selectedUser && (
          <div className="space-y-4">
            <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
              <div className="h-10 w-10 rounded-full bg-green-100 flex items-center justify-center text-green-700 font-bold shrink-0">
                {(selectedUser.firstName?.[0] || selectedUser.email[0] || '?').toUpperCase()}
              </div>
              <div>
                <p className="font-semibold text-gray-900">
                  {[selectedUser.firstName, selectedUser.lastName].filter(Boolean).join(' ') || selectedUser.email}
                </p>
                <p className="text-xs text-gray-500">{selectedUser.email}</p>
              </div>
              <button
                onClick={() => setSelectedUser(null)}
                className="ml-auto text-xs text-blue-600 hover:underline"
              >
                {t('admin.change', { defaultValue: 'Change' })}
              </button>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('admin.assign_role', { defaultValue: 'Assign Role' })}
              </label>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent outline-none bg-white text-sm"
              >
                {ASSIGNABLE_ROLES.map((r) => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </select>
            </div>
          </div>
        )}

        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
          >
            {t('common.cancel')}
          </button>
          {selectedUser && (
            <button
              onClick={handleAssign}
              disabled={assigning}
              className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors disabled:opacity-50 flex items-center gap-2"
            >
              {assigning && <Loader2 className="h-4 w-4 animate-spin" />}
              {t('admin.assign_button', { defaultValue: 'Assign to Tenant' })}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
