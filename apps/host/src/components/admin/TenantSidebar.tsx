import React, { useState, useMemo } from 'react';
import { Search, Plus, Building2 } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';

interface Tenant {
  tenant_id: string;
  tenant_name: string;
  plan_type: string;
  plan_level: number;
  status: string;
  created_at: string;
  user_count?: number;
}

interface TenantSidebarProps {
  tenants: Tenant[];
  selectedTenantId: string | null;
  loading: boolean;
  onSelect: (tenantId: string) => void;
  onCreateNew: () => void;
}

const PLAN_COLORS: Record<string, string> = {
  basic: 'bg-gray-100 text-gray-700',
  premium: 'bg-green-100 text-green-700',
  pro: 'bg-blue-100 text-blue-700',
  enterprise: 'bg-purple-100 text-purple-700',
};

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-green-500',
  suspended: 'bg-yellow-500',
  inactive: 'bg-red-500',
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
    <div className="w-80 min-w-[320px] border-r border-gray-200 bg-gray-50 flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-gray-200">
        <button
          onClick={onCreateNew}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-green-600 text-white font-semibold rounded-lg hover:bg-green-700 transition-colors"
        >
          <Plus className="h-5 w-5" />
          {t('admin.new_tenant', { defaultValue: 'New Tenant' })}
        </button>
      </div>

      {/* Search */}
      <div className="p-3 border-b border-gray-200">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            type="text"
            placeholder={t('admin.search_tenants', { defaultValue: 'Search tenants...' })}
            className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent outline-none bg-white"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {loading && tenants.length === 0 ? (
          <div className="p-6 text-center text-sm text-gray-500">
            {t('common.loading')}
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-6 text-center text-sm text-gray-500">
            {search.trim()
              ? t('admin.no_tenant_results', { defaultValue: 'No tenants match your search.' })
              : t('admin.no_tenants', { defaultValue: 'No tenants found.' })}
          </div>
        ) : (
          filtered.map((tenant) => (
            <button
              key={tenant.tenant_id}
              onClick={() => onSelect(tenant.tenant_id)}
              className={`w-full text-left p-3 border-b border-gray-100 hover:bg-gray-100 transition-colors ${
                selectedTenantId === tenant.tenant_id
                  ? 'bg-green-50 border-l-4 border-l-green-600'
                  : 'border-l-4 border-l-transparent'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <Building2 className="h-4 w-4 text-gray-400 shrink-0" />
                  <span className="font-semibold text-gray-900 text-sm truncate">
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
                  <div
                    className={`h-2 w-2 rounded-full ${
                      STATUS_COLORS[tenant.status] || STATUS_COLORS.inactive
                    }`}
                    title={tenant.status}
                  />
                </div>
              </div>
              <div className="flex items-center gap-3 mt-1 text-xs text-gray-500">
                <span className="font-mono truncate">{tenant.tenant_id}</span>
                {tenant.user_count != null && (
                  <span className="bg-gray-200 text-gray-600 px-1.5 py-0.5 rounded-full text-[10px] font-medium">
                    {tenant.user_count} {t('admin.users_count', { defaultValue: 'users' })}
                  </span>
                )}
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
};
