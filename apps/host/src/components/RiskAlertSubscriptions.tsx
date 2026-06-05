// =============================================================================
// Risk Alert Subscriptions Component
// =============================================================================

import React, { useState, useEffect } from 'react';
import { useAuth } from '@/context/KeycloakAuthContext';
import { useI18n } from '@/context/I18nContext';
import api from '@/services/api';
import type { RiskCatalog, RiskSubscription } from '@/types';
import { AlertTriangle, Bell, BellOff, Mail } from 'lucide-react';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';


interface RiskAlertSubscriptionsProps {
  readOnly?: boolean;
}

const DOMAIN_COLORS: Record<string, string> = {
  agronomic: 'bg-nkz-success-light text-green-800',
  energy: 'bg-nkz-warning-light text-yellow-800',
  robotic: 'bg-nkz-info-light text-blue-800',
  livestock: 'bg-orange-100 text-orange-800',
  other: 'bg-nkz-bg-secondary text-gray-800',
};

export const RiskAlertSubscriptions: React.FC<RiskAlertSubscriptionsProps> = ({ readOnly = false }) => {
  const { t } = useI18n();
  const { hasAnyRole } = useAuth();

  const [catalog, setCatalog] = useState<RiskCatalog[]>([]);
  const [subscriptions, setSubscriptions] = useState<RiskSubscription[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updating, setUpdating] = useState<string | null>(null);

  const canEdit = !readOnly && hasAnyRole(['PlatformAdmin', 'TenantAdmin']);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [catalogData, subsData] = await Promise.all([
        api.getRiskCatalog(),
        api.getRiskSubscriptions(),
      ]);
      setCatalog(catalogData);
      setSubscriptions(subsData);
    } catch (err: any) {
      if (import.meta.env.DEV) logger.error('Error loading risk data:', err);
      setError(t('settings.risks.load_error'));
    } finally {
      setLoading(false);
    }
  };

  const getSubscription = (riskCode: string): RiskSubscription | undefined => {
    return subscriptions.find(s => s.risk_code === riskCode);
  };

  const handleToggleSubscription = async (risk: RiskCatalog) => {
    if (!canEdit) return;
    const existing = getSubscription(risk.risk_code);
    setUpdating(risk.risk_code);

    try {
      if (existing) {
        const updated = await api.updateRiskSubscription(existing.id, {
          is_active: !existing.is_active,
        });
        setSubscriptions(prev =>
          prev.map(s => s.id === existing.id ? updated : s)
        );
      } else {
        const created = await api.createRiskSubscription({
          risk_code: risk.risk_code,
          is_active: true,
          user_threshold: risk.severity_levels.medium,
          notification_channels: { email: true, push: false },
        });
        setSubscriptions(prev => [...prev, created]);
      }
    } catch (err: any) {
      if (import.meta.env.DEV) logger.error('Error toggling subscription:', err);
      setError(t('settings.risks.update_error'));
      setTimeout(() => setError(null), 3000);
    } finally {
      setUpdating(null);
    }
  };

  const handleThresholdChange = async (risk: RiskCatalog, value: number) => {
    if (!canEdit) return;
    const existing = getSubscription(risk.risk_code);
    if (!existing) return;

    setUpdating(risk.risk_code);
    try {
      const updated = await api.updateRiskSubscription(existing.id, {
        user_threshold: value,
      });
      setSubscriptions(prev =>
        prev.map(s => s.id === existing.id ? updated : s)
      );
    } catch (err: any) {
      if (import.meta.env.DEV) logger.error('Error updating threshold:', err);
      setError(t('settings.risks.update_error'));
      setTimeout(() => setError(null), 3000);
    } finally {
      setUpdating(null);
    }
  };

  const handleToggleEmail = async (risk: RiskCatalog) => {
    if (!canEdit) return;
    const existing = getSubscription(risk.risk_code);
    if (!existing) return;

    setUpdating(risk.risk_code);
    try {
      const updated = await api.updateRiskSubscription(existing.id, {
        notification_channels: {
          ...existing.notification_channels,
          email: !existing.notification_channels.email,
        },
      });
      setSubscriptions(prev =>
        prev.map(s => s.id === existing.id ? updated : s)
      );
    } catch (err: any) {
      if (import.meta.env.DEV) logger.error('Error toggling email:', err);
      setError(t('settings.risks.update_error'));
      setTimeout(() => setError(null), 3000);
    } finally {
      setUpdating(null);
    }
  };

  // Group catalog by domain
  const grouped = catalog.reduce<Record<string, RiskCatalog[]>>((acc, risk) => {
    const domain = risk.risk_domain || 'other';
    if (!acc[domain]) acc[domain] = [];
    acc[domain].push(risk);
    return acc;
  }, {});

  if (loading) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-nkz-border p-6">
        <div className="text-center py-8">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto"></div>
          <p className="text-gray-600 mt-2">{t('settings.loading')}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-nkz-border p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 bg-orange-100 rounded-lg flex items-center justify-center">
          <AlertTriangle className="w-5 h-5 text-orange-600" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-gray-900">{t('settings.risks.title')}</h2>
          <p className="text-sm text-gray-600">{t('settings.risks.subtitle')}</p>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-nkz-error-light border border-red-200 rounded-lg">
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}

      {catalog.length === 0 ? (
        <div className="text-center py-8">
          <Bell className="w-12 h-12 text-nkz-muted mx-auto mb-4" />
          <p className="text-gray-600">{t('settings.risks.no_risks')}</p>
        </div>
      ) : (
        <div className="space-y-6">
          {Object.entries(grouped).map(([domain, risks]) => (
            <div key={domain}>
              <div className="flex items-center gap-2 mb-3">
                <span className={`px-2.5 py-1 text-xs font-medium rounded-full ${DOMAIN_COLORS[domain] || DOMAIN_COLORS.other}`}>
                  {t(`settings.risks.domain_${domain}`)}
                </span>
              </div>

              <div className="space-y-3">
                {risks.map((risk) => {
                  const sub = getSubscription(risk.risk_code);
                  const isActive = sub?.is_active ?? false;
                  const threshold = sub?.user_threshold ?? risk.severity_levels.medium;
                  const emailEnabled = sub?.notification_channels?.email ?? false;
                  const isUpdating = updating === risk.risk_code;

                  return (
                    <div
                      key={risk.risk_code}
                      className={`border rounded-lg p-4 transition ${
                        isActive ? 'border-orange-200 bg-orange-50/50' : 'border-nkz-border'
                      } ${isUpdating ? 'opacity-60' : ''}`}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex-1 min-w-0">
                          <h4 className="text-sm font-medium text-gray-900">{risk.risk_name}</h4>
                          {risk.risk_description && (
                            <p className="text-xs text-nkz-muted mt-0.5 truncate">{risk.risk_description}</p>
                          )}
                        </div>
                        <Button
                          onClick={() => handleToggleSubscription(risk)}
                          disabled={!canEdit || isUpdating}
                          className={`ml-4 p-2 rounded-lg transition ${
                            isActive
                              ? 'bg-orange-100 text-orange-600 hover:bg-orange-200'
                              : 'bg-nkz-bg-secondary text-nkz-muted hover:bg-gray-200'
                          } disabled:opacity-50 disabled:cursor-not-allowed`}
                          title={isActive ? t('settings.risks.deactivate') : t('settings.risks.activate')}
                        >
                          {isActive ? <Bell className="w-4 h-4" /> : <BellOff className="w-4 h-4" />}
                        </Button>
                      </div>

                      {isActive && sub && (
                        <div className="mt-3 pt-3 border-t border-nkz-border space-y-3">
                          <div>
                            <div className="flex items-center justify-between text-xs text-gray-600 mb-1">
                              <span>{t('settings.risks.threshold')}</span>
                              <span className="font-medium">{threshold}%</span>
                            </div>
                            <Input
                              type="range"
                              min="0"
                              max="100"
                              value={threshold}
                              onChange={(e: any) => handleThresholdChange(risk, parseInt(e.target.value))}
                              disabled={!canEdit || isUpdating}
                              className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-orange-500 disabled:cursor-not-allowed"
                            />
                          </div>

                          <div className="flex items-center justify-between">
                            <span className="text-xs text-gray-600 flex items-center gap-1">
                              <Mail className="w-3 h-3" />
                              {t('settings.risks.email_notifications')}
                            </span>
                            <Button
                              onClick={() => handleToggleEmail(risk)}
                              disabled={!canEdit || isUpdating}
                              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                                emailEnabled ? 'bg-blue-600' : 'bg-gray-300'
                              } disabled:opacity-50 disabled:cursor-not-allowed`}
                            >
                              <span
                                className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                                  emailEnabled ? 'translate-x-4.5' : 'translate-x-0.5'
                                }`}
                              />
                            </Button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
