import React, { useEffect, useState } from 'react';
import { useAuth } from '@/context/KeycloakAuthContext';
import { useI18n } from '@/context/I18nContext';
import api from '@/services/api';
import { Loader2, AlertTriangle } from 'lucide-react';

type VisibilityRules = Record<string, { hiddenRoles: string[] }>;

interface MarketplaceModule {
  id: string;
  display_name: string;
  name: string;
  description?: string;
  module_type?: string;
  required_plan_type?: string | null;
}

const MANAGEABLE_ROLES = ['Farmer', 'TechnicalConsultant', 'DeviceManager'];

export const ModuleVisibilitySettings: React.FC = () => {
  const { hasAnyRole } = useAuth();
  const { t } = useI18n();

  const [modules, setModules] = useState<MarketplaceModule[]>([]);
  const [rules, setRules] = useState<VisibilityRules>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const [marketplaceRes, visibilityRes] = await Promise.all([
          api.get('/api/modules/marketplace'),
          api.get('/api/modules/visibility'),
        ]);

        const marketplace: MarketplaceModule[] = Array.isArray(marketplaceRes.data)
          ? marketplaceRes.data
          : [];

        const rawVisibility: Record<string, { hiddenRoles?: string[] }> =
          visibilityRes.data && typeof visibilityRes.data === 'object'
            ? visibilityRes.data
            : {};

        const normalised: VisibilityRules = {};
        Object.entries(rawVisibility).forEach(([moduleId, cfg]) => {
          if (!moduleId || !cfg) return;
          const rawHidden = Array.isArray(cfg.hiddenRoles) ? cfg.hiddenRoles : [];
          normalised[moduleId] = {
            hiddenRoles: rawHidden.filter((r): r is string => typeof r === 'string'),
          };
        });

        setModules(marketplace);
        setRules(normalised);
      } catch (err: any) {
        const msg =
          err?.response?.data?.error ||
          err?.response?.data?.message ||
          err?.message ||
          'Failed to load module visibility settings';
        setError(msg);
      } finally {
        setLoading(false);
      }
    };

    void load();
  }, []);

  const isTenantAdmin = hasAnyRole(['TenantAdmin']);
  const isPlatformAdmin = hasAnyRole(['PlatformAdmin']);

  // Only TenantAdmin or PlatformAdmin can see this card
  if (!isTenantAdmin && !isPlatformAdmin) {
    return null;
  }

  const toggleRoleVisibility = (moduleId: string, role: string) => {
    setRules((prev) => {
      const current = prev[moduleId] || { hiddenRoles: [] };
      const isHidden = current.hiddenRoles.includes(role);
      const nextHidden = isHidden
        ? current.hiddenRoles.filter((r) => r !== role)
        : [...current.hiddenRoles, role];
      return {
        ...prev,
        [moduleId]: { hiddenRoles: nextHidden },
      };
    });
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      // Backend accepts either top-level map or { rules: ... }
      await api.put('/api/modules/visibility', { rules });
      setSuccess(t('settings.module_visibility.saved', { defaultValue: 'Module visibility updated successfully.' }));
    } catch (err: any) {
      const msg =
        err?.response?.data?.error ||
        err?.response?.data?.message ||
        err?.message ||
        t('settings.module_visibility.save_error', { defaultValue: 'Failed to save module visibility.' });
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  const isReadOnly = !isTenantAdmin && isPlatformAdmin;

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">
            {t('settings.module_visibility.title', { defaultValue: 'Module visibility by role' })}
          </h2>
          <p className="text-sm text-gray-600">
            {t('settings.module_visibility.subtitle', {
              defaultValue: 'Control which modules are visible for each user role in this tenant.',
            })}
          </p>
        </div>
        {loading && (
          <div className="flex items-center gap-2 text-gray-500 text-sm">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>{t('settings.loading', { defaultValue: 'Loading…' })}</span>
          </div>
        )}
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {success && (
        <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-800">
          {success}
        </div>
      )}

      {isReadOnly && (
        <div className="mb-4 p-3 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5" />
          <span>
            {t('settings.module_visibility.platform_admin_hint', {
              defaultValue:
                'You are viewing module visibility for this tenant. Only the Tenant Admin can change these settings.',
            })}
          </span>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="text-left py-2 pr-4 text-gray-600 font-medium">
                {t('settings.module_visibility.module', { defaultValue: 'Module' })}
              </th>
              {MANAGEABLE_ROLES.map((role) => (
                <th key={role} className="text-center px-3 py-2 text-gray-600 font-medium">
                  {role}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {modules.map((mod) => {
              const rule = rules[mod.id] || { hiddenRoles: [] };
              return (
                <tr key={mod.id} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="py-2 pr-4">
                    <div className="flex flex-col">
                      <span className="font-medium text-gray-900">
                        {mod.display_name || mod.name || mod.id}
                      </span>
                      {mod.description && (
                        <span className="text-xs text-gray-500 line-clamp-2">{mod.description}</span>
                      )}
                    </div>
                  </td>
                  {MANAGEABLE_ROLES.map((role) => {
                    const hidden = rule.hiddenRoles.includes(role);
                    return (
                      <td key={role} className="text-center px-3 py-2">
                        <button
                          type="button"
                          disabled={saving || isReadOnly}
                          onClick={() => toggleRoleVisibility(mod.id, role)}
                          className={`inline-flex items-center justify-center w-9 h-9 rounded-full border text-xs font-medium transition ${
                            hidden
                              ? 'bg-gray-200 border-gray-300 text-gray-700'
                              : 'bg-emerald-50 border-emerald-200 text-emerald-700'
                          } ${saving || isReadOnly ? 'opacity-60 cursor-not-allowed' : 'hover:shadow-sm'}`}
                          title={
                            hidden
                              ? t('settings.module_visibility.hidden_tooltip', {
                                  defaultValue: 'Hidden for this role',
                                })
                              : t('settings.module_visibility.visible_tooltip', {
                                  defaultValue: 'Visible for this role',
                                })
                          }
                        >
                          {hidden
                            ? t('settings.module_visibility.hidden_short', { defaultValue: 'Off' })
                            : t('settings.module_visibility.visible_short', { defaultValue: 'On' })}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
            {modules.length === 0 && !loading && (
              <tr>
                <td
                  colSpan={1 + MANAGEABLE_ROLES.length}
                  className="py-4 text-center text-gray-500 text-sm"
                >
                  {t('settings.module_visibility.no_modules', {
                    defaultValue: 'No modules available for this tenant yet.',
                  })}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {!isReadOnly && (
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {saving
              ? t('settings.saving', { defaultValue: 'Saving…' })
              : t('settings.save', { defaultValue: 'Save' })}
          </button>
        </div>
      )}
    </div>
  );
};

export default ModuleVisibilitySettings;

