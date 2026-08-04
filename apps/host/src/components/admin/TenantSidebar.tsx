import React, { useState, useMemo } from 'react';
import { Search, Plus, Building2 } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import { Button, Input } from '@nekazari/ui-kit';

/* eslint-disable @typescript-eslint/no-explicit-any */
interface Tenant {
  tenant_id: string;
  tenant_name: string;
  plan_type: string;
  plan_level: number;
  status: string;
  created_at: string;
  user_count?: number;
  deleted_at?: string | null;
  deleted_by?: string | null;
}

interface TenantSidebarProps {
  tenants: Tenant[];
  selectedTenantId: string | null;
  loading: boolean;
  onSelect: (tenantId: string) => void;
  onCreateNew: () => void;
}

const PLAN_COLORS: Record<string, string> = {
  basic: 'bg-nkz-surface-sunken text-nkz-text-secondary',
  premium: 'bg-nkz-accent-soft text-nkz-accent-strong',
  pro: 'bg-nkz-info-soft text-nkz-info-strong',
  enterprise: 'bg-nkz-warning-soft text-nkz-warning-strong',
};

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-nkz-success',
  suspended: 'bg-nkz-warning',
  inactive: 'bg-nkz-danger',
};

export const TenantSidebar: React.FC<TenantSidebarProps> = ({
  tenants,
  selectedTenantId,
  loading,
  onSelect,
  onCreateNew,
}) => {
  const { t } = useI18n();
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    if (!search.trim()) return tenants;
    const q = search.toLowerCase();
    return tenants.filter(
      (t) =>
        t.tenant_name.toLowerCase().includes(q) ||
        t.tenant_id.toLowerCase().includes(q)
    );
  }, [tenants, search]);

  return (
    <div className="w-80 min-w-[320px] border-r border-nkz-border bg-nkz-surface-sunken flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-nkz-border">
        <Button
          onClick={onCreateNew}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-nkz-accent-base text-nkz-text-on-accent font-semibold rounded-nkz-lg hover:bg-nkz-accent-strong transition-colors"
        >
          <Plus className="h-5 w-5" />
          {t('admin.new_tenant', { defaultValue: 'New Tenant' })}
        </Button>
      </div>

      {/* Search */}
      <div className="p-3 border-b border-nkz-border">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-nkz-text-muted" />
          <Input
            type="text"
            placeholder={t('admin.search_tenants', { defaultValue: 'Search tenants...' })}
            className="w-full pl-9 pr-3 py-2 text-nkz-sm border border-nkz-border rounded-nkz-lg focus:ring-2 focus:ring-nkz-accent-base focus:border-transparent outline-none bg-nkz-surface"
            value={search}
            onChange={(e: any) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {loading && tenants.length === 0 ? (
          <div className="p-6 text-center text-nkz-sm text-nkz-text-secondary">
            {t('common.loading')}
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-6 text-center text-nkz-sm text-nkz-text-secondary">
            {search.trim()
              ? t('admin.no_tenant_results', { defaultValue: 'No tenants match your search.' })
              : t('admin.no_tenants', { defaultValue: 'No tenants found.' })}
          </div>
        ) : (
          filtered.map((tenant) => (
            <Button
              key={tenant.tenant_id}
              onClick={() => onSelect(tenant.tenant_id)}
              className={`w-full text-left p-3 border-b border-nkz-border hover:bg-nkz-canvas transition-colors ${
                selectedTenantId === tenant.tenant_id
                  ? 'bg-nkz-accent-soft border-l-4 border-l-nkz-accent-base'
                  : 'border-l-4 border-l-transparent'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <Building2 className="h-4 w-4 text-nkz-text-muted shrink-0" />
                  <span className="font-semibold text-nkz-text-primary text-nkz-sm truncate">
                    {tenant.tenant_name}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded font-bold uppercase ${
                      PLAN_COLORS[tenant.plan_type] || PLAN_COLORS.basic
                    }`}
                  >
                    {tenant.plan_type}
                  </span>
                  {tenant.deleted_at && (
                    <>
                      {(() => {
                        const daysAgo = Math.floor((Date.now() - new Date(tenant.deleted_at!).getTime()) / (1000 * 60 * 60 * 24));
                        const isPurgable = daysAgo > 45;
                        return (
                          <span
                            className={`text-xs font-bold px-2 py-1 rounded ${
                              isPurgable
                                ? 'bg-nkz-danger-soft text-nkz-danger-strong'
                                : 'bg-nkz-warning-soft text-nkz-warning-strong'
                            }`}
                            title={isPurgable ? t('admin.purgable_tooltip') : t('admin.suspended_since', { days: daysAgo, admin: tenant.deleted_by || '?' })}
                          >
                            {isPurgable ? t('admin.purgable') : t('admin.suspended')}
                          </span>
                        );
                      })()}
                    </>
                  )}
                  <div
                    className={`h-2 w-2 rounded-full ${
                      STATUS_COLORS[tenant.status] || STATUS_COLORS.inactive
                    }`}
                    title={tenant.status}
                  />
                </div>
              </div>
              <div className="flex items-center gap-3 mt-1 text-nkz-xs text-nkz-text-secondary">
                <span className="font-mono truncate">{tenant.tenant_id}</span>
                {tenant.user_count != null && (
                  <span className="bg-nkz-border text-nkz-text-secondary px-1.5 py-0.5 rounded-full text-[10px] font-medium">
                    {tenant.user_count} {t('admin.users_count', { defaultValue: 'users' })}
                  </span>
                )}
              </div>
            </Button>
          ))
        )}
      </div>
    </div>
  );
};
