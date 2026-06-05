// =============================================================================
// Risk Summary Card — Dashboard widget showing active risks for the tenant
// =============================================================================

import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { ShieldAlert, ShieldCheck, ArrowRight, RefreshCw } from 'lucide-react';
import { api } from '@/services/api';
import type { RiskState } from '@/types';
import { useTranslation } from '@nekazari/sdk';
import { Button } from '@nekazari/ui-kit';

type TFn = (key: string, opts?: Record<string, unknown>) => string;

// Map risk codes to translation keys
const RISK_KEY: Record<string, string> = {
  MILDIU:            'dashboard.risks.mildiu',
  SPRAY_SUITABILITY: 'dashboard.risks.spray',
  FROST:             'dashboard.risks.frost',
  WIND_SPRAY:        'dashboard.risks.wind_spray',
  WATER_STRESS:      'dashboard.risks.water_stress',
  ENERGY_RISK:       'dashboard.risks.energy',
  ROBOT_FAILURE:     'dashboard.risks.robot_failure',
  GDD_PRAYS_OLEAE:   'dashboard.risks.prays_oleae',
  GDD_LOBESIA_1ST:   'dashboard.risks.lobesia_1st',
  GDD_LOBESIA_2ND:   'dashboard.risks.lobesia_2nd',
};

interface SeverityCfg { badge: string; bar: string; dot: string; key: string; }

const SEVERITY_CONFIG: Record<string, SeverityCfg> = {
  critical: { badge: 'bg-nkz-error-light text-red-800 dark:bg-red-900/40 dark:text-red-300',         bar: 'bg-nkz-error-light0',    dot: 'bg-nkz-error-light0',    key: 'dashboard.risks.severity.critical' },
  high:     { badge: 'bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300', bar: 'bg-orange-500', dot: 'bg-orange-500', key: 'dashboard.risks.severity.high' },
  medium:   { badge: 'bg-nkz-warning-light text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300', bar: 'bg-nkz-warning-light0', dot: 'bg-nkz-warning-light0', key: 'dashboard.risks.severity.medium' },
  low:      { badge: 'bg-nkz-info-light text-nkz-info dark:bg-blue-900/40 dark:text-blue-300',     bar: 'bg-blue-400',   dot: 'bg-blue-400',   key: 'dashboard.risks.severity.low' },
};

function computeSeverity(score: number, severity: string | null | undefined): string {
  if (severity && severity in SEVERITY_CONFIG) return severity;
  if (score >= 95) return 'critical';
  if (score >= 80) return 'high';
  if (score >= 60) return 'medium';
  return 'low';
}

/** Extract a short readable name from a full NGSI-LD URN.
 *  "urn:ngsi-ld:AgriParcel:north-field" → "north-field"
 */
function shortEntityName(entityId: string): string {
  if (!entityId) return entityId;
  const parts = entityId.split(':');
  return parts[parts.length - 1] || entityId;
}

function riskLabel(t: TFn, code: string): string {
  const k = RISK_KEY[code];
  return k ? t(k) : code.replace(/_/g, ' ');
}

const REFRESH_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

export const RiskSummaryCard: React.FC = () => {
  const { t } = useTranslation('common');
  const navigate = useNavigate();
  const [states, setStates] = useState<RiskState[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api.getRiskStates({ limit: 30 })
      .then(data => {
        setStates(data);
        setLastUpdated(new Date());
      })
      .catch(() => setStates([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [load]);

  // Derive per-severity counts
  const counts = { critical: 0, high: 0, medium: 0, low: 0 };
  states.forEach(s => {
    const sev = computeSeverity(s.probability_score, s.severity);
    counts[sev as keyof typeof counts] = (counts[sev as keyof typeof counts] ?? 0) + 1;
  });

  const alertCount = counts.critical + counts.high;
  const visible = states.slice(0, 6);

  const headerGradient = alertCount > 0
    ? 'from-red-500 to-orange-500'
    : 'from-emerald-500 to-green-600';

  return (
    <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-lg overflow-hidden flex flex-col h-full">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className={`bg-gradient-to-r ${headerGradient} px-6 py-4 flex items-center justify-between`}>
        <h2 className="text-xl font-bold text-white flex items-center gap-2">
          {alertCount > 0
            ? <ShieldAlert className="w-6 h-6" />
            : <ShieldCheck className="w-6 h-6" />}
          {t('dashboard.risk_summary')}
        </h2>
        <div className="flex items-center gap-2">
          {alertCount > 0 && (
            <span className="bg-white/20 text-white text-sm font-semibold px-3 py-0.5 rounded-full">
              {t(alertCount === 1 ? 'dashboard.risks.alert_count_one' : 'dashboard.risks.alert_count_other', { count: alertCount })}
            </span>
          )}
          <Button
            onClick={load}
            disabled={loading}
            className="text-white/70 hover:text-white transition"
            title={t('refresh')}
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {/* ── Severity summary chips ─────────────────────────────────────────── */}
      {!loading && states.length > 0 && (
        <div className="flex gap-2 flex-wrap px-6 pt-4">
          {(['critical', 'high', 'medium', 'low'] as const).map(sev => {
            const n = counts[sev];
            if (n === 0) return null;
            const cfg = SEVERITY_CONFIG[sev];
            return (
              <span key={sev} className={`text-xs font-semibold px-2.5 py-0.5 rounded-full ${cfg.badge}`}>
                {n} {t(cfg.key)}
              </span>
            );
          })}
        </div>
      )}

      {/* ── Content ────────────────────────────────────────────────────────── */}
      <div className="p-6 flex-1 flex flex-col">
        {loading ? (
          <div className="flex-1 flex items-center justify-center py-8">
            <div className="w-8 h-8 border-4 border-orange-400 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : states.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center text-center py-8 text-nkz-muted dark:text-nkz-muted">
            <ShieldCheck className="w-12 h-12 text-emerald-400 mb-3" />
            <p className="text-sm font-medium text-gray-700 dark:text-gray-200">
              {t('dashboard.risks.no_alerts')}
            </p>
            <p className="text-xs mt-1 opacity-60">
              {t('dashboard.risks.next_eval')}
            </p>
          </div>
        ) : (
          <div className="space-y-3 flex-1">
            {visible.map(state => {
              const sev = computeSeverity(state.probability_score, state.severity);
              const cfg = SEVERITY_CONFIG[sev] ?? SEVERITY_CONFIG.low;
              return (
                <div key={state.id} className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    {/* Risk name */}
                    <div className="flex items-center gap-2 min-w-0">
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${cfg.dot}`} />
                      <span className="text-sm font-medium text-gray-900 dark:text-white truncate">
                        {riskLabel(t, state.risk_code)}
                      </span>
                    </div>
                    {/* Score + badge */}
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      <span className="text-xs text-nkz-muted dark:text-nkz-muted tabular-nums">
                        {Math.round(state.probability_score)}%
                      </span>
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${cfg.badge}`}>
                        {t(cfg.key)}
                      </span>
                    </div>
                  </div>
                  {/* Entity name */}
                  <p className="text-xs text-nkz-muted dark:text-nkz-muted pl-4 truncate">
                    {shortEntityName(state.entity_id)}
                  </p>
                  {/* Score bar */}
                  <div className="w-full bg-nkz-bg-secondary dark:bg-gray-700 rounded-full h-1.5">
                    <div
                      className={`h-1.5 rounded-full transition-all duration-500 ${cfg.bar}`}
                      style={{ width: `${Math.min(state.probability_score, 100)}%` }}
                    />
                  </div>
                </div>
              );
            })}

            {states.length > 6 && (
              <p className="text-xs text-nkz-muted dark:text-nkz-muted text-center pt-1">
                {t('dashboard.risks.more_evaluations', { count: states.length - 6 })}
              </p>
            )}
          </div>
        )}

        {/* ── Footer ─────────────────────────────────────────────────────── */}
        <div className="mt-4 space-y-2">
          {lastUpdated && (
            <p className="text-xs text-center text-nkz-muted dark:text-nkz-muted">
              {t('dashboard.risks.updated_time', { time: lastUpdated.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' }) })}
            </p>
          )}
          <Button
            onClick={() => navigate('/risks')}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-nkz-bg-secondary dark:bg-gray-700 hover:bg-nkz-bg-secondary dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 text-sm font-medium rounded-xl transition"
          >
            {t('dashboard.risks.view_panel')}
            <ArrowRight className="w-4 h-4" />
          </Button>
        </div>
      </div>
    </div>
  );
};
