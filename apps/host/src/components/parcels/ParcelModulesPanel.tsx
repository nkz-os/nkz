// =============================================================================
// Parcel Modules Panel - Per-parcel module activation (list + toggle + status)
// =============================================================================
// Lets a TenantAdmin/PlatformAdmin activate/deactivate the tenant's installed
// modules for a specific parcel. Backed by entity-manager's parcel_activation
// state machine: GET/POST /api/entities/parcels/{id}/modules[/{moduleId}/activate|deactivate].
import { logger } from '@/utils/logger';

import React, { useCallback, useEffect, useState } from 'react';
import { Layers, ChevronDown, ChevronUp } from 'lucide-react';
import { Badge, Button, SettingsList, SettingsItem, Switch, Spinner, Tooltip } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';
import { useToastContext } from '@/context/ToastContext';
import { useModules } from '@/context/ModuleContext';
import { useAuth } from '@/context/KeycloakAuthContext';
import { parcelApi, type ParcelModuleActivation } from '@/services/parcelApi';

interface ParcelModulesPanelProps {
    parcelId: string;
}

type SetupStatus = ParcelModuleActivation['setup_status'];
// Mirrors ui-kit's internal BadgeIntent union (not exported by the package).
type StatusBadgeIntent = 'default' | 'positive' | 'warning' | 'negative' | 'info';

const STATUS_INTENT: Record<SetupStatus, StatusBadgeIntent> = {
    ok: 'positive',
    pending: 'warning',
    error: 'negative',
};

export const ParcelModulesPanel: React.FC<ParcelModulesPanelProps> = ({ parcelId }) => {
    const { t } = useI18n();
    const toast = useToastContext();
    const { modules } = useModules();
    const { hasAnyRole } = useAuth();
    // Backend (entity-manager) rejects activate/deactivate for other roles with 403.
    const canManage = hasAnyRole(['PlatformAdmin', 'TenantAdmin']);

    const [activations, setActivations] = useState<Record<string, ParcelModuleActivation>>({});
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState(false);
    const [collapsed, setCollapsed] = useState(false);
    const [busyModuleId, setBusyModuleId] = useState<string | null>(null);

    // Only tenant-installed modules that declare a setup_parcel_url support
    // per-parcel activation (contract: entity-manager parcel_activation.dispatch_to_module).
    const activatableModules = modules.filter((m) => !!m.metadata?.setup_parcel_url);

    const loadActivations = useCallback(async () => {
        if (!parcelId) return;
        setLoading(true);
        setLoadError(false);
        try {
            const rows = await parcelApi.getParcelModules(parcelId);
            const map: Record<string, ParcelModuleActivation> = {};
            rows.forEach((row) => { map[row.module_id] = row; });
            setActivations(map);
        } catch (err) {
            logger.warn('[ParcelModulesPanel] Failed to load parcel modules:', err);
            setLoadError(true);
        } finally {
            setLoading(false);
        }
    }, [parcelId]);

    useEffect(() => {
        loadActivations();
    }, [loadActivations]);

    const handleToggle = async (moduleId: string, moduleLabel: string, nextEnabled: boolean) => {
        setBusyModuleId(moduleId);
        try {
            const result = nextEnabled
                ? await parcelApi.activateParcelModule(parcelId, moduleId)
                : await parcelApi.deactivateParcelModule(parcelId, moduleId);
            if (result?.setup_status === 'error') {
                toast.error(t('parcelModules.setupFailed', { module: moduleLabel }));
            }
        } catch (err: unknown) {
            logger.warn(`[ParcelModulesPanel] ${nextEnabled ? 'activate' : 'deactivate'} failed for ${moduleId}:`, err);
            const message =
                (err as { response?: { data?: { error?: string } } })?.response?.data?.error ||
                t('parcelModules.setupFailed', { module: moduleLabel });
            toast.error(message);
        } finally {
            await loadActivations();
            setBusyModuleId(null);
        }
    };

    const statusLabel = (status: SetupStatus): string => {
        switch (status) {
            case 'ok': return t('parcelModules.statusOk');
            case 'pending': return t('parcelModules.statusPending');
            case 'error': return t('parcelModules.statusError');
            default: return status;
        }
    };

    return (
        <div className="bg-nkz-info-soft rounded-lg border border-blue-200">
            <Button
                className="w-full flex items-center justify-between p-4 text-left"
                onClick={() => setCollapsed((c) => !c)}
            >
                <h4 className="text-sm font-semibold text-blue-900 flex items-center gap-2">
                    <Layers className="w-4 h-4" />
                    {t('parcelModules.title')}
                </h4>
                {collapsed ? (
                    <ChevronDown className="w-4 h-4 text-nkz-info" />
                ) : (
                    <ChevronUp className="w-4 h-4 text-nkz-info" />
                )}
            </Button>

            {!collapsed && (
                <div className="px-4 pb-4">
                    {loading ? (
                        <div className="flex items-center gap-2 text-sm text-gray-600">
                            <Spinner size="sm" />
                            <span>{t('parcelModules.loading')}</span>
                        </div>
                    ) : loadError ? (
                        <p className="text-sm text-nkz-danger-strong italic">{t('parcelModules.loadError')}</p>
                    ) : activatableModules.length === 0 ? (
                        <p className="text-sm text-nkz-muted italic">{t('parcelModules.noModules')}</p>
                    ) : (
                        <SettingsList>
                            {activatableModules.map((m) => {
                                const activation = activations[m.id];
                                const status = activation?.setup_status;
                                // Only show the switch as ON when setup actually
                                // succeeded — enabled=true with status='error' means
                                // the user asked to activate it but it didn't work;
                                // showing ON next to the error badge reads as broken.
                                const enabled = (activation?.enabled ?? false) && status !== 'error';
                                const isBusy = busyModuleId === m.id;
                                const label = m.label || m.displayName;

                                const badge = status ? (
                                    <Badge intent={STATUS_INTENT[status]}>{statusLabel(status)}</Badge>
                                ) : null;

                                return (
                                    <SettingsItem
                                        key={m.id}
                                        label={label}
                                        control={
                                            <div className="flex items-center gap-2">
                                                {badge && status === 'error' && activation?.last_error ? (
                                                    <Tooltip
                                                        content={t('parcelModules.lastError', { error: activation.last_error })}
                                                        side="top"
                                                    >
                                                        <span>{badge}</span>
                                                    </Tooltip>
                                                ) : (
                                                    badge
                                                )}
                                                {canManage && status === 'error' && (
                                                    <Button
                                                        className="text-xs text-nkz-info hover:underline"
                                                        onClick={() => handleToggle(m.id, label, true)}
                                                        disabled={isBusy}
                                                    >
                                                        {t('retry')}
                                                    </Button>
                                                )}
                                                {isBusy ? (
                                                    <Spinner size="sm" />
                                                ) : (
                                                    <Switch
                                                        checked={enabled}
                                                        onChange={(next) => handleToggle(m.id, label, next)}
                                                        disabled={isBusy || !canManage}
                                                        label={enabled ? t('active') : t('inactive')}
                                                        labelPosition="left"
                                                    />
                                                )}
                                            </div>
                                        }
                                    />
                                );
                            })}
                        </SettingsList>
                    )}
                </div>
            )}
        </div>
    );
};
