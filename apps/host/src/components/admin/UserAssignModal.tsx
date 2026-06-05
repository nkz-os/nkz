import React, { useState, useEffect } from 'react';
import { X, Search, Loader2, Mail } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import client from '@/services/api';
import { Button, Input } from '@nekazari/ui-kit';

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
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-nkz-modal" onClick={onClose}>
        <div className="bg-nkz-surface-raised rounded-nkz-xl shadow-nkz-xl p-6 w-full max-w-md text-center" onClick={(e) => e.stopPropagation()}>
          <div className="text-nkz-accent-base text-nkz-lg font-semibold mb-2">{successMsg}</div>
          <p className="text-nkz-sm text-nkz-text-secondary">
            {selectedUser.email} &rarr; {role}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-nkz-modal" onClick={onClose}>
      <div className="bg-nkz-surface-raised rounded-nkz-xl shadow-nkz-xl p-6 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-nkz-lg font-semibold text-nkz-text-primary">
            {t('admin.assign_user_title', { defaultValue: 'Assign Existing User' })}
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

        {/* Search */}
        {!selectedUser && (
          <>
            <div className="relative mb-4">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-nkz-text-muted" />
              <Input
                type="text"
                placeholder={t('admin.search_users_placeholder', { defaultValue: 'Search by name or email...' })}
                className="w-full pl-9 pr-4 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none text-nkz-sm bg-nkz-surface"
                value={search}
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                onChange={(e: any) => setSearch(e.target.value)}
                autoFocus
              />
            </div>

            {/* Results */}
            <div className="max-h-60 overflow-y-auto border border-nkz-border rounded-nkz-lg">
              {searching && (
                <div className="p-4 text-center text-nkz-sm text-nkz-text-secondary">
                  {t('common.loading')}
                </div>
              )}
              {!searching && debouncedSearch.length < 2 && (
                <div className="p-4 text-center text-nkz-sm text-nkz-text-muted">
                  {t('admin.type_to_search', { defaultValue: 'Type at least 2 characters to search' })}
                </div>
              )}
              {!searching && debouncedSearch.length >= 2 && results.length === 0 && (
                <div className="p-4 text-center text-nkz-sm text-nkz-text-secondary">
                  {t('admin.no_user_results', { defaultValue: 'No users found.' })}
                </div>
              )}
              {results.map((user) => (
                <Button
                  key={user.id}
                  onClick={() => setSelectedUser(user)}
                  className="w-full text-left p-3 hover:bg-nkz-canvas border-b border-nkz-border last:border-b-0 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <div className="h-8 w-8 rounded-full bg-nkz-accent-soft flex items-center justify-center text-nkz-accent-strong font-bold text-nkz-xs shrink-0">
                      {(user.firstName?.[0] || user.email[0] || '?').toUpperCase()}
                    </div>
                    <div className="min-w-0">
                      <p className="text-nkz-sm font-semibold text-nkz-text-primary truncate">
                        {[user.firstName, user.lastName].filter(Boolean).join(' ') || user.email}
                      </p>
                      <p className="text-nkz-xs text-nkz-text-secondary flex items-center gap-1">
                        <Mail className="h-3 w-3" /> {user.email}
                      </p>
                      {user.tenant && (
                        <span className="text-[10px] text-nkz-text-muted">
                          {t('admin.current_tenant', { defaultValue: 'Current tenant' })}: {user.tenant}
                        </span>
                      )}
                    </div>
                  </div>
                </Button>
              ))}
            </div>
          </>
        )}

        {/* Selected user + role */}
        {selectedUser && (
          <div className="space-y-4">
            <div className="flex items-center gap-3 p-3 bg-nkz-surface-sunken rounded-nkz-lg">
              <div className="h-10 w-10 rounded-full bg-nkz-accent-soft flex items-center justify-center text-nkz-accent-strong font-bold shrink-0">
                {(selectedUser.firstName?.[0] || selectedUser.email[0] || '?').toUpperCase()}
              </div>
              <div>
                <p className="font-semibold text-nkz-text-primary">
                  {[selectedUser.firstName, selectedUser.lastName].filter(Boolean).join(' ') || selectedUser.email}
                </p>
                <p className="text-nkz-xs text-nkz-text-secondary">{selectedUser.email}</p>
              </div>
              <Button
                onClick={() => setSelectedUser(null)}
                className="ml-auto text-nkz-xs text-nkz-info hover:underline"
              >
                {t('admin.change', { defaultValue: 'Change' })}
              </Button>
            </div>

            <div>
              <label className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
                {t('admin.assign_role', { defaultValue: 'Assign Role' })}
              </label>
              <select
                value={role}
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                onChange={(e: any) => setRole(e.target.value)}
                className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface text-nkz-sm"
              >
                {ASSIGNABLE_ROLES.map((r) => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </select>
            </div>
          </div>
        )}

        <div className="flex justify-end gap-3 mt-6">
          <Button
            onClick={onClose}
            className="px-4 py-2 text-nkz-text-secondary bg-nkz-surface-sunken rounded-nkz-lg hover:bg-nkz-border transition-colors"
          >
            {t('common.cancel')}
          </Button>
          {selectedUser && (
            <Button
              onClick={handleAssign}
              disabled={assigning}
              className="px-4 py-2 bg-nkz-accent-base text-nkz-text-on-accent rounded-nkz-lg hover:bg-nkz-accent-strong transition-colors disabled:opacity-50 flex items-center gap-2"
            >
              {assigning && <Loader2 className="h-4 w-4 animate-spin" />}
              {t('admin.assign_button', { defaultValue: 'Assign to Tenant' })}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
};
