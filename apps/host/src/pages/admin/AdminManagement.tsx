import React, { useState, useEffect, useCallback } from 'react';
import {
/* eslint-disable @typescript-eslint/no-explicit-any */
  Users, Ticket, Search, Plus,
  Trash2, ShieldCheck, RefreshCcw,
  Settings2, Shield, Key, ScrollText,
  FileText, Activity, Box, Monitor, Puzzle, UserPlus, UserCheck,
} from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import client from '@/services/api';
import { format } from 'date-fns';
import { useModules } from '@/context/ModuleContext';
import { SlotRenderer } from '@/components/SlotRenderer';
import { Button, Input } from '@nekazari/ui-kit';
import { UserTable, type UserRow } from '@/components/admin/UserTable';
import { UserEditModal } from '@/components/admin/UserEditModal';
import { useUserActions } from '@/components/admin/useUserActions';
import { TenantSidebar } from '@/components/admin/TenantSidebar';
import { TenantCreateModal } from '@/components/admin/TenantCreateModal';
import { TenantConfigForm } from '@/components/admin/TenantConfigForm';
import { UserCreateModal } from '@/components/admin/UserCreateModal';
import { UserAssignModal } from '@/components/admin/UserAssignModal';
import { SuspendTenantDialog } from '@/components/admin/SuspendTenantDialog';
import { RestoreTenantButton } from '@/components/admin/RestoreTenantButton';
import { PurgeTenantDialog } from '@/components/admin/PurgeTenantDialog';

// Legacy imports
import { LimitsManagement } from '@/components/LimitsManagement';
import { TermsManagement } from '@/components/TermsManagement';
import { PlatformApiCredentials } from '@/components/PlatformApiCredentials';
import { AuditLogsPanel } from '@/components/AuditLogsPanel';
import { GlobalAssetManager } from '@/components/Admin/GlobalAssetManager';
import { useNotification } from '@/hooks/useNotification';
import { logger } from '@/utils/logger';


interface Tenant {
  tenant_id: string;
  tenant_name: string;
  plan_type: string;
  plan_level: number;
  status: string;
  created_at: string;
  deleted_at?: string | null;
  deleted_by?: string | null;
}

interface User {
  /** Keycloak user id (required for admin delete) */
  id: string;
  email: string;
  username: string;
  firstName: string;
  lastName: string;
  enabled: boolean;
  roles: string[];
  tenant?: string;
  createdAt?: number;
}

const PLATFORM_USERS_PAGE_SIZE = 100;

/** Map Keycloak admin API user object to table row (stable id = Keycloak id). */
function mapKeycloakUserToRow(u: Record<string, unknown>): User {
  const firstName = String(u.firstName ?? '');
  const lastName = String(u.lastName ?? '');
  const tenantRaw = u.tenant_id;
  const tenant =
    typeof tenantRaw === 'string' && tenantRaw.trim() !== ''
      ? tenantRaw.trim()
      : undefined;
  const ts = u.createdTimestamp;
  let createdAt: number | undefined;
  if (typeof ts === 'number' && !Number.isNaN(ts)) {
    createdAt = ts < 1e12 ? ts * 1000 : ts;
  } else if (typeof ts === 'string') {
    const n = parseInt(ts, 10);
    if (!Number.isNaN(n)) createdAt = n;
  }
  const rolesRaw = u.roles;
  const roles = Array.isArray(rolesRaw)
    ? rolesRaw.filter((r): r is string => typeof r === 'string')
    : [];


  return {
    id: String(u.id ?? ''),
    email: String(u.email ?? ''),
    username: String(u.username ?? ''),
    firstName,
    lastName,
    enabled: u.enabled !== false,
    roles,
    tenant,
    createdAt,
  };
}

interface ActivationCode {
  id: number;
  code: string;
  email: string;
  plan: string;
  plan_level: number;
  status: string;
  expires_at: string;
}

const PLATFORM_ADMIN_ROLES = [
  { value: 'PlatformAdmin', label: 'Platform Admin', description: 'Full platform access' },
  { value: 'TenantAdmin', label: 'Tenant Admin', description: 'Tenant-level management' },
  { value: 'GestorCUE', label: 'Gestor CUE', description: 'CUE cross-tenant field notebook manager' },
  { value: 'TechnicalConsultant', label: 'Technical Consultant', description: 'Technical data and modules access' },
  { value: 'Farmer', label: 'Farmer', description: 'Basic dashboard access' },
];

export const AdminManagement: React.FC = () => {
  const { t } = useI18n();
  const { showNotification } = useNotification();
  const { modules } = useModules();

  // Global tab state: null = master-detail (sidebar + tenant/user panels)
  const [globalTab, setGlobalTab] = useState<string | null>(null);

  // Tenant data
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantsLoading, setTenantsLoading] = useState(false);
  const [selectedTenantId, setSelectedTenantId] = useState<string | null>(null);
  const [tenantTab, setTenantTab] = useState<'users' | 'config'>('users');

  // Platform users (Keycloak directory)
  const [users, setUsers] = useState<User[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [usersSearch, setUsersSearch] = useState('');
  const [usersSearchDebounced, setUsersSearchDebounced] = useState('');
  const [userHasMore, setUserHasMore] = useState(false);
  const [loadingMoreUsers, setLoadingMoreUsers] = useState(false);
  const [usersRefreshNonce, setUsersRefreshNonce] = useState(0);
  const usersNextFirstRef = React.useRef(0);

  // Modal visibility
  const [showCreateTenant, setShowCreateTenant] = useState(false);
  const [showCreateUser, setShowCreateUser] = useState(false);
  const [showAssignUser, setShowAssignUser] = useState(false);

  // Tenant lifecycle dialogs
  const [suspendDialogOpen, setSuspendDialogOpen] = useState(false);
  const [purgeDialogOpen, setPurgeDialogOpen] = useState(false);

  // Role editing
  const [editingUser, setEditingUser] = useState<UserRow | null>(null);
  const [showEditModal, setShowEditModal] = useState(false);

  // Activation codes
  const [activations, setActivations] = useState<ActivationCode[]>([]);
  const [activationsLoading, setActivationsLoading] = useState(false);
  const [showCodeModal, setShowCodeModal] = useState(false);
  const [codeForm, setCodeForm] = useState({ email: '', plan: 'premium' });

  // Landing mode
  const [landingMode, setLandingMode] = useState<'standard' | 'commercial'>('standard');
  const [landingModeLoading, setLandingModeLoading] = useState(false);
  const [landingModeSaving, setLandingModeSaving] = useState(false);
  const [landingMessage, setLandingMessage] = useState<string>('');

  const userActions = useUserActions({ apiBase: 'admin' });

  // Find modules that provide admin-tab slots
  const adminTabModules = Array.isArray(modules)
    ? modules.filter(m => m.viewerSlots?.['admin-tab'] && m.viewerSlots['admin-tab'].length > 0)
    : [];

  // --- Data loading ---

  const loadTenants = useCallback(async () => {
    setTenantsLoading(true);
    try {
      const response = await client.get('/api/admin/tenants');
      const data = response.data;
      const raw = Array.isArray(data) ? data : (data.tenants || []);
      setTenants(
        raw.map((t: Record<string, unknown>) => ({
          tenant_id: String(t.tenant_id ?? t.id ?? ''),
          tenant_name: String(t.tenant_name ?? t.name ?? t.tenant_id ?? t.id ?? ''),
          plan_type: String(t.plan_type ?? t.plan ?? 'basic'),
          plan_level: typeof t.plan_level === 'number' ? t.plan_level : 0,
          status: String(t.status ?? 'active'),
          created_at:
            typeof t.created_at === 'string'
              ? t.created_at
              : t.created_at
                ? String(t.created_at)
                : '',
        }))
      );
    } catch (error) {
      logger.error('Error loading tenants:', error);
    } finally {
      setTenantsLoading(false);
    }
  }, []);

  const loadActivations = useCallback(async () => {
    setActivationsLoading(true);
    try {
      const response = await client.get('/api/admin/activations');
      const data = response.data;
      setActivations(Array.isArray(data) ? data : (data.activations || data.codes || []));
    } catch (error) {
      logger.error('Error loading activations:', error);
    } finally {
      setActivationsLoading(false);
    }
  }, []);

  const loadLandingMode = useCallback(async () => {
    setLandingModeLoading(true);
    try {
      const response = await client.get('/api/public/platform-settings');
      const mode = String(response?.data?.landing_mode || '').toLowerCase() === 'commercial' ? 'commercial' : 'standard';
      setLandingMode(mode);
      setLandingMessage('');
    } catch (error) {
      setLandingMessage('Could not read current landing mode. Using standard as fallback.');
      setLandingMode('standard');
    } finally {
      setLandingModeLoading(false);
    }
  }, []);

  // Load tenants on mount
  useEffect(() => {
    void loadTenants();
  }, [loadTenants]);

  // Load activations when switching to activations tab
  useEffect(() => {
    if (globalTab === 'activations') {
      void loadActivations();
    }
  }, [globalTab, loadActivations]);

  // Load landing mode when switching to platform tab
  useEffect(() => {
    if (globalTab === 'platform') {
      loadLandingMode();
    }
  }, [globalTab, loadLandingMode]);

  // Debounce user search (only in master-detail mode)
  useEffect(() => {
    if (globalTab !== null) return;
    const timer = window.setTimeout(() => setUsersSearchDebounced(usersSearch.trim()), 400);
    return () => window.clearTimeout(timer);
  }, [usersSearch, globalTab]);

  // Load platform users (only in master-detail mode)
  useEffect(() => {
    if (globalTab !== null) return;
    let cancelled = false;
    (async () => {
      setUsersLoading(true);
      setUsersError(null);
      try {
        const params = new URLSearchParams({
          first: '0',
          max: String(PLATFORM_USERS_PAGE_SIZE),
        });
        if (usersSearchDebounced) {
          params.set('search', usersSearchDebounced);
        }
        const response = await client.get(`/api/admin/users?${params.toString()}`);
        if (cancelled) return;
        if (!response.data?.success) {
          throw new Error(response.data?.error || 'Failed to load users');
        }
        const raw = (response.data.users || []) as Record<string, unknown>[];
        const mapped = raw.map(mapKeycloakUserToRow);
        setUsers(mapped);
        const pag = response.data.pagination;
        const start = typeof pag?.first === 'number' ? pag.first : 0;
        usersNextFirstRef.current = start + mapped.length;
        setUserHasMore(Boolean(pag?.has_more));
      } catch (err: unknown) {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : String(err);
          setUsersError(msg);
          setUsers([]);
          setUserHasMore(false);
        }
      } finally {
        if (!cancelled) setUsersLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [globalTab, usersSearchDebounced, usersRefreshNonce]);

  // --- User actions ---

  const handleLoadMoreUsers = async () => {
    if (!userHasMore || loadingMoreUsers || globalTab !== null) return;
    setLoadingMoreUsers(true);
    setUsersError(null);
    try {
      const first = usersNextFirstRef.current;
      const params = new URLSearchParams({
        first: String(first),
        max: String(PLATFORM_USERS_PAGE_SIZE),
      });

      if (usersSearchDebounced) {
        params.set('search', usersSearchDebounced);
      }
      const response = await client.get(`/api/admin/users?${params.toString()}`);
      if (!response.data?.success) {
        throw new Error(response.data?.error || 'Failed to load users');
      }
      const raw = (response.data.users || []) as Record<string, unknown>[];
      const mapped = raw.map(mapKeycloakUserToRow);
      setUsers((prev) => [...prev, ...mapped]);
      const pag = response.data.pagination;
      const start = typeof pag?.first === 'number' ? pag.first : first;
      usersNextFirstRef.current = start + mapped.length;
      setUserHasMore(Boolean(pag?.has_more));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setUsersError(msg);
    } finally {
      setLoadingMoreUsers(false);
    }
  };

  const handleDeleteUser = async (userId: string, email: string) => {
    const ok = await userActions.deleteUser(userId, email);
    if (ok) {
      setUsers(users.filter(u => u.id !== userId));
    }
  };

  const handleResetPassword = async (userId: string) => {
    await userActions.resetPassword(userId);
  };

  const handleEditRoles = (user: UserRow) => {
    setEditingUser(user);
    setShowEditModal(true);
  };

  const handleSaveRoles = async (userId: string, data: { roles: string[]; firstName?: string; lastName?: string }) => {
    const ok = await userActions.updateRoles(userId, data.roles);
    if (ok) {
      setUsers(users.map(u => u.id === userId ? { ...u, roles: data.roles } : u));
      setShowEditModal(false);
      setEditingUser(null);
    }
  };

  // --- Activation code actions ---

  const handleGenerateCode = async () => {
    if (!codeForm.email) return;
    try {
      setActivationsLoading(true);
      await client.createActivationCode({
        email: codeForm.email,
        plan: codeForm.plan,
      });
      setShowCodeModal(false);
      setCodeForm({ email: '', plan: 'premium' });
      showNotification({ type: 'error', message: t('admin.code_generated') });
      void loadActivations();
    } catch (error: any) {
      // error detail available via error.response.data.error
      showNotification({ type: 'error', message: t('admin.code_generate_error') });
    } finally {
      setActivationsLoading(false);
    }
  };

  const handleRevokeCode = async (codeId: number) => {
    if (!window.confirm(t('admin.confirm_revoke_code'))) {
      return;
    }
    try {
      await client.delete(`/api/admin/activations/${codeId}`);
      setActivations(activations.map(a => a.id === codeId ? { ...a, status: 'revoked' } : a));
      showNotification({ type: 'error', message: t('admin.code_revoked') });
    } catch (error: any) {
      // error detail available via error.response.data.error
      showNotification({ type: 'error', message: t('admin.code_revoke_error') });
    }
  };

  // --- Landing mode actions ---

  const handleLandingModeToggle = async () => {
    setLandingModeSaving(true);
    setLandingMessage('');
    const nextMode = landingMode === 'standard' ? 'commercial' : 'standard';
    try {
      await client.put('/api/admin/platform-settings/landing-mode', { landing_mode: nextMode });
      setLandingMode(nextMode);
      setLandingMessage(`Landing mode updated to "${nextMode}". This affects new visits to "/".`);
    } catch (error: any) {
      const detail = error?.response?.data?.error || 'Failed to update landing mode.';
      setLandingMessage(String(detail));
    } finally {
      setLandingModeSaving(false);
    }
  };

  // --- Refresh ---

  const handleRefresh = () => {
    if (globalTab === null) {
      void loadTenants();
      setUsersRefreshNonce((n) => n + 1);
    } else if (globalTab === 'activations') {
      void loadActivations();
    } else if (globalTab === 'platform') {
      loadLandingMode();
    }
  };

  // --- Filtered users (when a tenant is selected) ---

  const displayUsers = selectedTenantId
    ? users.filter(u => u.tenant === selectedTenantId)
    : users;

  const selectedTenant = selectedTenantId
    ? tenants.find(t => t.tenant_id === selectedTenantId) ?? null
    : null;

  // --- Global tab config ---

  const globalTabs = [
    { id: null, label: 'Gestión Tenants', icon: Shield },
    { id: 'activations', label: 'Códigos NEK', icon: Ticket },
    { id: 'limits', label: 'Límites', icon: Activity },
    { id: 'terms', label: 'Términos', icon: FileText },
    { id: 'apis', label: 'APIs Plataforma', icon: Key },
    { id: 'platform', label: 'Plataforma', icon: Monitor },
    { id: 'logs', label: 'Logs', icon: ScrollText },
    { id: 'assets', label: 'Assets', icon: Box },
  ];

  const showLoading = globalTab === null
    ? (usersLoading && users.length === 0)
    : (globalTab === 'activations' && activationsLoading && activations.length === 0);

  // ========================
  // RENDER
  // ========================

  return (
    <div className="h-[calc(100vh-64px)] flex flex-col">
      {/* -- Top bar -- */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-nkz-border bg-nkz-surface shrink-0">
        <h1 className="text-nkz-xl font-bold text-nkz-text-primary flex items-center gap-2">
          <Shield className="text-nkz-accent-base h-6 w-6" />
          Nekazari Control Center
        </h1>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Refrescar datos"
          onClick={handleRefresh}
          className="!px-2 !py-2"
        >
          <RefreshCcw className={`h-5 w-5 ${showLoading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {/* -- Global tabs -- */}
      <div className="flex border-b border-nkz-border overflow-x-auto no-scrollbar bg-nkz-surface shrink-0">
        {globalTabs.map((tab) => (
          <Button
            key={String(tab.id)}
            variant="ghost"
            onClick={() => {
              setGlobalTab(tab.id);
              if (tab.id !== null) {
                setSelectedTenantId(null);
              }
            }}
            className={`px-6 py-3 border-b-2 -mb-[2px] whitespace-nowrap rounded-none ${
              globalTab === tab.id
                ? 'border-nkz-accent-base text-nkz-accent-base'
                : 'border-transparent text-nkz-text-secondary'
            }`}
          >
            <tab.icon className="h-5 w-5" />
            {tab.label}
          </Button>
        ))}
        {/* Module-contributed admin tabs */}
        {adminTabModules.map((module) => (
          <Button
            key={`module-tab-${module.id}`}
            variant="ghost"
            onClick={() => setGlobalTab(`module-${module.id}`)}
            className={`px-6 py-3 border-b-2 -mb-[2px] whitespace-nowrap rounded-none ${
              globalTab === `module-${module.id}`
                ? 'border-nkz-info text-nkz-info'
                : 'border-transparent text-nkz-text-secondary'
            }`}
          >
            <Puzzle className="h-5 w-5" />
            {module.displayName || module.name}
          </Button>
        ))}
      </div>

      {/* -- Body -- */}
      <div className="flex flex-1">
        {globalTab === null ? (
          /* ----- Master-Detail Layout ----- */
          <>
            {/* Left: TenantSidebar */}
            <TenantSidebar
              tenants={tenants}
              selectedTenantId={selectedTenantId}
              loading={tenantsLoading}
              onSelect={(id) => setSelectedTenantId(id)}
              onCreateNew={() => setShowCreateTenant(true)}
            />

            {/* Right: Detail panel */}
            <div className="flex-1 overflow-auto bg-nkz-canvas">
              {selectedTenant ? (
                /* ---- Tenant-scoped view ---- */
                <div>
                  {/* Tenant header */}
                  <div className="bg-nkz-surface border-b border-nkz-border px-6 py-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <h2 className="text-nkz-xl font-bold text-nkz-text-primary flex items-center gap-2">
                          <ShieldCheck className="text-nkz-accent-base h-5 w-5" />
                          {selectedTenant.tenant_name}
                        </h2>
                        <p className="text-nkz-sm text-nkz-text-secondary font-mono mt-0.5">
                          ID: {selectedTenant.tenant_id}
                        </p>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className={`text-xs font-bold px-2 py-1 rounded uppercase ${
                          selectedTenant.plan_type === 'enterprise' ? 'bg-nkz-warning-soft text-nkz-warning-strong' : 'bg-nkz-accent-soft text-nkz-accent-strong'
                        }`}>
                          {selectedTenant.plan_type}
                        </span>
                        {selectedTenant.deleted_at ? (
                          <div className="flex items-center gap-2">
                            <RestoreTenantButton
                              tenantId={selectedTenant.tenant_id}
                              onRestored={() => { loadTenants(); }}
                            />
                            <Button
                              variant="danger"
                              size="sm"
                              onClick={() => setPurgeDialogOpen(true)}
                            >
                              {t('admin.purge_tenant_button')}
                            </Button>
                          </div>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={t('admin.suspend_tenant_button')}
                            onClick={() => setSuspendDialogOpen(true)}
                            className="!px-2 !py-2"
                          >
                            <Trash2 className="h-5 w-5" />
                          </Button>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Tenant tabs */}
                  <div className="flex border-b border-nkz-border bg-nkz-surface px-6">
                    <Button
                      variant="ghost"
                      onClick={() => setTenantTab('users')}
                      className={`px-4 py-3 text-nkz-sm border-b-2 -mb-[2px] rounded-none ${
                        tenantTab === 'users'
                          ? 'border-nkz-accent-base text-nkz-accent-base'
                          : 'border-transparent text-nkz-text-secondary'
                      }`}
                    >
                      <Users className="h-4 w-4" />
                      {t('admin.users_tab', { defaultValue: 'Usuarios' })}
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => setTenantTab('config')}
                      className={`px-4 py-3 text-nkz-sm border-b-2 -mb-[2px] rounded-none ${
                        tenantTab === 'config'
                          ? 'border-nkz-accent-base text-nkz-accent-base'
                          : 'border-transparent text-nkz-text-secondary'
                      }`}
                    >
                      <Settings2 className="h-4 w-4" />
                      {t('admin.config_tab', { defaultValue: 'Config' })}
                    </Button>
                  </div>

                  {/* Tenant tab content */}
                  <div className="p-6">
                    {tenantTab === 'users' && (
                      <div>
                        {/* Create / Assign buttons */}
                        <div className="flex gap-3 mb-4">
                          <Button
                            variant="primary"
                            size="sm"
                            onClick={() => setShowCreateUser(true)}
                          >
                            <UserPlus className="h-4 w-4" />
                            {t('admin.create_user_button', { defaultValue: 'Create User' })}
                          </Button>
                          <Button
                            variant="primary"
                            size="sm"
                            onClick={() => setShowAssignUser(true)}
                            className="bg-nkz-info hover:bg-nkz-info-strong"
                          >
                            <UserCheck className="h-4 w-4" />
                            {t('admin.assign_user_button', { defaultValue: 'Assign User' })}
                          </Button>
                        </div>

                        {/* Users table */}
                        {usersError && (
                          <div className="mb-4 px-4 py-3 bg-nkz-danger-soft border border-nkz-danger-soft rounded-nkz-lg text-nkz-danger-strong text-sm">
                            {usersError}
                          </div>
                        )}
                        <div className="bg-nkz-surface rounded-nkz-xl shadow-nkz-sm border border-nkz-border overflow-x-auto">
                          {usersLoading && displayUsers.length === 0 ? (
                            <div className="p-12 text-center">
                              <RefreshCcw className="h-10 w-10 text-nkz-accent-base animate-spin mx-auto mb-4" />
                              <p className="text-nkz-text-secondary">{t('common.loading')}</p>
                            </div>
                          ) : (
                            <>
                              <UserTable
                                users={displayUsers}
                                loading={usersLoading}
                                actions={{
                                  editRoles: true,
                                  resetPassword: true,
                                  deleteUser: true,
                                }}
                                onEditRoles={handleEditRoles}
                                onResetPassword={handleResetPassword}
                                onDeleteUser={handleDeleteUser}
                              />
                              {userHasMore && (
                                <div className="p-4 border-t border-nkz-border flex justify-center bg-nkz-surface-sunken">
                                  <Button
                                    variant="secondary"
                                    onClick={() => void handleLoadMoreUsers()}
                                    disabled={loadingMoreUsers}
                                  >
                                    {loadingMoreUsers ? 'Cargando...' : 'Cargar mas usuarios'}
                                  </Button>
                                </div>
                              )}
                              {displayUsers.length === 0 && !usersLoading && (
                                <div className="p-12 text-center bg-nkz-surface-sunken">
                                  <Users className="h-12 w-12 text-nkz-text-muted mx-auto mb-4" />
                                  <p className="text-nkz-text-secondary font-medium">
                                    {t('admin.no_users', { defaultValue: 'No users found for this tenant.' })}
                                  </p>
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      </div>
                    )}

                    {tenantTab === 'config' && (
                      <TenantConfigForm tenantId={selectedTenant.tenant_id} />
                    )}
                  </div>
                </div>
              ) : (
                /* ---- All Platform Users (no tenant selected) ---- */
                <div className="p-6">
                  <div className="mb-6">
                    <h2 className="text-xl font-bold text-nkz-text-primary">
                      {t('admin.all_platform_users', { defaultValue: 'All Platform Users' })}
                    </h2>
                    <p className="text-sm text-nkz-text-secondary mt-1">
                      {t('admin.all_platform_users_desc', { defaultValue: 'Global user directory across all tenants.' })}
                    </p>
                  </div>

                  {/* Search */}
                  <div className="bg-nkz-surface p-4 rounded-nkz-xl shadow-nkz-sm border border-nkz-border mb-6">
                    <div className="relative">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-nkz-text-muted" />
                      <Input
                        type="text"
                        placeholder={t('admin.search_users', { defaultValue: 'Search users...' })}
                        className="w-full pl-10 pr-4 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none"
                        value={usersSearch}
                        onChange={(e: any) => setUsersSearch(e.target.value)}
                      />
                    </div>
                  </div>

                  {/* Users table */}
                  {usersError && (
                    <div className="mb-4 px-4 py-3 bg-nkz-danger-soft border border-nkz-danger-soft rounded-nkz-lg text-nkz-danger-strong text-sm">
                      {usersError}
                    </div>
                  )}
                  <div className="bg-nkz-surface rounded-nkz-xl shadow-nkz-sm border border-nkz-border overflow-x-auto">
                    {usersLoading && users.length === 0 ? (
                      <div className="p-12 text-center">
                        <RefreshCcw className="h-10 w-10 text-nkz-accent-base animate-spin mx-auto mb-4" />
                        <p className="text-nkz-text-secondary">{t('common.loading')}</p>
                      </div>
                    ) : (
                      <>
                        <UserTable
                          users={displayUsers}
                          loading={usersLoading}
                          actions={{
                            editRoles: true,
                            resetPassword: true,
                            deleteUser: true,
                          }}
                          onEditRoles={handleEditRoles}
                          onResetPassword={handleResetPassword}
                          onDeleteUser={handleDeleteUser}
                        />
                        {userHasMore && (
                          <div className="p-4 border-t border-nkz-border flex justify-center bg-nkz-surface-sunken">
                            <Button
                              variant="secondary"
                              onClick={() => void handleLoadMoreUsers()}
                              disabled={loadingMoreUsers}
                            >
                              {loadingMoreUsers ? 'Cargando...' : 'Cargar mas usuarios'}
                            </Button>
                          </div>
                        )}
                        {displayUsers.length === 0 && !usersLoading && (
                          <div className="p-12 text-center bg-nkz-surface-sunken">
                            <Users className="h-12 w-12 text-nkz-text-muted mx-auto mb-4" />
                            <p className="text-nkz-text-secondary font-medium">
                              {t('admin.no_users_global', { defaultValue: 'No users found.' })}
                            </p>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          </>
        ) : (
          /* ----- Global Tab Content (no sidebar) ----- */
          <div className="flex-1 overflow-auto bg-nkz-canvas">
            {globalTab === 'activations' && (
              <div className="p-6">
                {/* Actions bar */}
                <div className="bg-nkz-surface p-4 rounded-nkz-xl shadow-nkz-sm border border-nkz-border mb-6 flex flex-wrap gap-4 items-center justify-between">
                  <h2 className="text-lg font-bold text-nkz-text-primary">
                    {t('admin.activation_codes', { defaultValue: 'Activation Codes' })}
                  </h2>
                  <Button
                    variant="primary"
                    onClick={() => setShowCodeModal(true)}
                  >
                    <Plus className="h-5 w-5" />
                    {t('admin.generate_code')}
                  </Button>
                </div>

                {/* Activations table */}
                <div className="bg-nkz-surface rounded-nkz-xl shadow-nkz-sm border border-nkz-border overflow-x-auto">
                  {activationsLoading && activations.length === 0 ? (
                    <div className="p-12 text-center">
                      <RefreshCcw className="h-10 w-10 text-nkz-accent-base animate-spin mx-auto mb-4" />
                      <p className="text-nkz-text-secondary">{t('common.loading')}</p>
                    </div>
                  ) : (
                    <table className="w-full text-left">
                      <thead className="bg-nkz-surface-sunken border-b border-nkz-border">
                        <tr>
                          <th className="px-6 py-4 font-semibold text-nkz-text-primary text-sm">{t('admin.nek_code')}</th>
                          <th className="px-6 py-4 font-semibold text-nkz-text-primary text-sm">{t('admin.dest_email')}</th>
                          <th className="px-6 py-4 font-semibold text-nkz-text-primary text-sm">{t('admin.plan_type')}</th>
                          <th className="px-6 py-4 font-semibold text-nkz-text-primary text-sm">{t('admin.status')}</th>
                          <th className="px-6 py-4 font-semibold text-nkz-text-primary text-sm">{t('admin.expiration')}</th>
                          <th className="px-6 py-4 font-semibold text-nkz-text-primary text-sm text-right">{t('admin.actions')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100">
                        {activations.map(activation => (
                          <tr key={activation.id} className="hover:bg-nkz-surface-sunken transition-colors">
                            <td className="px-6 py-4 font-mono font-bold text-nkz-text-primary">{activation.code}</td>
                            <td className="px-6 py-4 text-sm text-nkz-text-secondary">{activation.email}</td>
                            <td className="px-6 py-4">
                              <span className="text-xs font-bold uppercase text-nkz-info">{activation.plan}</span>
                            </td>
                            <td className="px-6 py-4">
                              <span className={`text-xs font-medium px-2 py-1 rounded ${
                                activation.status === 'active' ? 'bg-nkz-accent-soft text-nkz-accent-strong' :
                                activation.status === 'revoked' ? 'bg-nkz-danger-soft text-nkz-danger-strong' :
                                'bg-nkz-surface-sunken text-nkz-text-primary'
                              }`}>
                                {activation.status === 'active' ? t('admin.status_used') :
                                 activation.status === 'revoked' ? t('admin.status_revoked') :
                                 t('admin.status_pending')}
                              </span>
                            </td>
                            <td className="px-6 py-4 text-sm text-nkz-text-secondary">
                              {format(new Date(activation.expires_at), 'dd/MM/yyyy')}
                            </td>
                            <td className="px-6 py-4 text-right">
                              <Button
                                variant="ghost"
                                size="sm"
                                aria-label={t('admin.revoke_code')}
                                onClick={() => handleRevokeCode(activation.id)}
                                className="!px-2 !py-2"
                              >
                                <Trash2 className="h-5 w-5" />
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  {!activationsLoading && activations.length === 0 && (
                    <div className="p-12 text-center bg-nkz-surface-sunken">
                      <Ticket className="h-12 w-12 text-nkz-text-muted mx-auto mb-4" />
                      <p className="text-nkz-text-secondary font-medium">
                        {t('admin.no_activations', { defaultValue: 'No activation codes found.' })}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {globalTab === 'limits' && (
              <div className="p-6">
                <LimitsManagement />
              </div>
            )}

            {globalTab === 'terms' && (
              <div className="p-6">
                <TermsManagement />
              </div>
            )}

            {globalTab === 'apis' && (
              <div className="p-6">
                <PlatformApiCredentials />
              </div>
            )}

            {globalTab === 'platform' && (
              <div className="p-6">
                <div className="max-w-2xl rounded-nkz-xl border border-nkz-border bg-nkz-surface p-5 shadow-nkz-sm">
                  <h3 className="text-lg font-semibold text-nkz-text-primary mb-2">Landing page mode</h3>
                  <p className="text-sm text-nkz-text-secondary mb-4">
                    Switch between the standard OSS landing and the commercial landing. This is a global platform setting and only affects new visits to the public home route.
                  </p>
                  <div className="flex items-center justify-between gap-4 rounded-nkz-lg bg-nkz-surface-sunken border border-nkz-border p-4">
                    <div>
                      <p className="text-sm font-medium text-nkz-text-primary">
                        Current mode: <span className="uppercase">{landingModeLoading ? 'loading...' : landingMode}</span>
                      </p>
                      <p className="text-xs text-nkz-text-secondary">
                        standard = OSS landing, commercial = branded/commercial landing
                      </p>
                    </div>
                    <Button
                      variant="primary"
                      onClick={handleLandingModeToggle}
                      disabled={landingModeLoading || landingModeSaving}
                    >
                      {landingModeSaving ? 'Saving...' : `Switch to ${landingMode === 'standard' ? 'commercial' : 'standard'}`}
                    </Button>
                  </div>
                  {landingMessage && (
                    <p className="mt-3 text-sm text-nkz-text-primary">{landingMessage}</p>
                  )}
                </div>
              </div>
            )}

            {globalTab === 'logs' && (
              <AuditLogsPanel />
            )}

            {globalTab === 'assets' && (
              <div className="p-6">
                <GlobalAssetManager />
              </div>
            )}

            {/* Module-contributed admin tabs */}
            {adminTabModules.map((module) => (
              globalTab === `module-${module.id}` && (
                <div key={`module-content-${module.id}`} className="p-6">
                  <SlotRenderer
                    slot="admin-tab"
                  />
                </div>
              )
            ))}
          </div>
        )}
      </div>

      {/* ===== Modals ===== */}

      {/* Create Tenant Modal */}
      {showCreateTenant && (
        <TenantCreateModal
          onClose={() => setShowCreateTenant(false)}
          onCreated={() => {
            setShowCreateTenant(false);
            void loadTenants();
          }}
        />
      )}

      {/* Create User Modal */}
      {showCreateUser && selectedTenantId && (
        <UserCreateModal
          tenantId={selectedTenantId}
          onClose={() => setShowCreateUser(false)}
          onCreated={() => {
            setShowCreateUser(false);
            setUsersRefreshNonce((n) => n + 1);
          }}
        />
      )}

      {/* Assign User Modal */}
      {showAssignUser && selectedTenantId && (
        <UserAssignModal
          tenantId={selectedTenantId}
          onClose={() => setShowAssignUser(false)}
          onAssigned={() => {
            setShowAssignUser(false);
            setUsersRefreshNonce((n) => n + 1);
          }}
        />
      )}

      {/* Edit User Roles Modal */}
      {showEditModal && editingUser && (
        <UserEditModal
          user={editingUser}
          availableRoles={PLATFORM_ADMIN_ROLES}
          isPlatformContext={true}
          loading={userActions.loading}
          onSave={handleSaveRoles}
          onClose={() => {
            setShowEditModal(false);
            setEditingUser(null);
            userActions.clearMessages();
          }}
        />
      )}

      {/* Generate Code Modal */}
      {showCodeModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowCodeModal(false)}>
          <div className="bg-nkz-surface-raised rounded-nkz-xl shadow-nkz-xl p-6 w-full max-w-md" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-nkz-text-primary mb-4">{t('admin.generate_code')}</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-nkz-text-primary mb-1">{t('admin.email_prompt')}</label>
                <Input
                  type="email"
                  value={codeForm.email}
                  onChange={(e: any) => setCodeForm(f => ({ ...f, email: e.target.value }))}
                  className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none"
                  placeholder="user@example.com"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-nkz-text-primary mb-1">{t('admin.plan_type')}</label>
                <select
                  value={codeForm.plan}
                  onChange={(e: any) => setCodeForm(f => ({ ...f, plan: e.target.value }))}
                  className="w-full px-3 py-2 border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
                >
                  <option value="premium">Premium</option>
                  <option value="enterprise">Enterprise</option>
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <Button
                variant="secondary"
                onClick={() => setShowCodeModal(false)}
              >
                {t('common.cancel')}
              </Button>
              <Button
                variant="primary"
                onClick={handleGenerateCode}
                disabled={!codeForm.email || activationsLoading}
              >
                {activationsLoading ? t('common.loading') : t('admin.generate_code')}
              </Button>
            </div>
          </div>
        </div>
      )}
      {/* Tenant lifecycle dialogs */}
      {suspendDialogOpen && selectedTenantId && (
        <SuspendTenantDialog
          tenantId={selectedTenantId}
          tenantName={selectedTenant?.tenant_name || selectedTenantId}
          onClose={() => setSuspendDialogOpen(false)}
          onSuspended={() => {
            setSuspendDialogOpen(false);
            loadTenants();
          }}
        />
      )}

      {purgeDialogOpen && selectedTenantId && (
        <PurgeTenantDialog
          tenantId={selectedTenantId}
          tenantName={selectedTenant?.tenant_name || selectedTenantId}
          onClose={() => setPurgeDialogOpen(false)}
          onPurged={() => {
            setPurgeDialogOpen(false);
            loadTenants();
            setSelectedTenantId(null);
          }}
        />
      )}
    </div>
  );
};
