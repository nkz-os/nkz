import React from 'react';
import { CalendarClock, ShieldCheck, Zap, Users, Bot, Gauge, MapPin, RefreshCw } from 'lucide-react';
import type { TenantLimits, TenantUsageStats } from '@/types';
import { ProgressBar } from './ProgressBar';
import { useI18n } from '@/context/I18nContext';

interface PlanSummaryCardProps {
  planType?: string | null;
  daysRemaining?: number | null;
  expiresAt?: string | null;
  limits?: TenantLimits;
  usage?: TenantUsageStats;
  updatedAt?: string;
}

type TFn = (key: string, opts?: Record<string, unknown>) => string;

const formatPlanName = (t: TFn, planType?: string | null): string => {
  const basic = t('dashboard.plan.basic');
  if (!planType) return basic;
  const normalized = planType.toLowerCase().replace(/[-_]+/g, ' ').trim();
  const planMap: Record<string, string> = {
    'basic': basic,
    'basico': basic,
    'advance': t('dashboard.plan.advanced'),
    'avanzado': t('dashboard.plan.advanced'),
    'advanced': t('dashboard.plan.advanced'),
    'enterprise': t('dashboard.plan.enterprise'),
    'empresarial': t('dashboard.plan.enterprise'),
  };
  if (planMap[normalized]) return planMap[normalized];
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
};

const formatExpiration = (t: TFn, daysRemaining?: number | null): { label: string; tone: string } => {
  if (typeof daysRemaining !== 'number') {
    return { label: t('dashboard.plan.no_expiration_data'), tone: 'text-nkz-muted' };
  }
  if (daysRemaining <= 0) {
    return { label: t('dashboard.plan.expired'), tone: 'text-nkz-error font-semibold' };
  }
  const key = daysRemaining === 1 ? 'dashboard.plan.expires_one' : 'dashboard.plan.expires_other';
  const label = t(key, { days: daysRemaining });
  const tone = daysRemaining <= 3
    ? 'text-nkz-error font-semibold'
    : daysRemaining <= 7
      ? 'text-orange-500 font-semibold'
      : 'text-emerald-600 font-semibold';
  return { label, tone };
};

export const PlanSummaryCard: React.FC<PlanSummaryCardProps> = ({
  planType,
  daysRemaining,
  expiresAt,
  limits,
  usage,
  updatedAt,
}) => {
  const { t } = useI18n();
  const expiration = formatExpiration(t, daysRemaining);
  const planName = formatPlanName(t, planType ?? limits?.planType ?? undefined);
  const robotsInUse = usage?.robots ?? 0;
  const sensorsInUse = usage?.sensors ?? 0;
  const areaInUse = usage?.areaHectares ?? 0;

  const robotsLimitFrag = limits?.maxRobots ? `de ${limits.maxRobots}` : '';
  const sensorsLimitFrag = limits?.maxSensors ? `de ${limits.maxSensors}` : '';
  const areaLimitFrag = limits?.maxAreaHectares ? `de ${limits.maxAreaHectares} ha` : '';

  return (
    <div className="mb-8 rounded-2xl border border-nkz-border dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm overflow-hidden">
      <div className="bg-gradient-to-r from-emerald-500 via-emerald-600 to-emerald-700 px-6 py-4 text-white flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div className="flex items-center gap-3">
          <ShieldCheck className="w-6 h-6" />
          <div>
            <h2 className="text-xl font-semibold">{t('dashboard.plan.title')}</h2>
            <p className="text-sm text-emerald-100">{t('dashboard.plan.description')}</p>
          </div>
        </div>
        <div className="flex items-center gap-4 text-sm">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4" />
            <span className="font-medium">{planName}</span>
          </div>
          <div className="flex items-center gap-2">
            <CalendarClock className="w-4 h-4" />
            <span className={expiration.tone}>{expiration.label}</span>
          </div>
          {expiresAt && (
            <span className="hidden md:block text-emerald-100">({new Date(expiresAt).toLocaleDateString('es-ES')})</span>
          )}
        </div>
      </div>

      <div className="px-6 py-5 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="rounded-xl border border-nkz-border dark:border-gray-700 p-4 bg-nkz-bg-secondary dark:bg-gray-700/50">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-nkz-muted dark:text-nkz-muted">{t('dashboard.plan.users')}</div>
            <Users className="w-4 h-4 text-nkz-muted" />
          </div>
            <div className="text-2xl font-semibold text-gray-900 dark:text-gray-100">
            {limits?.maxUsers ?? '—'}
          </div>
          <p className="text-xs text-nkz-muted dark:text-nkz-muted">{t('dashboard.plan.users_allowed')}</p>
        </div>

        <div className="rounded-xl border border-nkz-border dark:border-gray-700 p-4 bg-nkz-bg-secondary dark:bg-gray-700/50">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-nkz-muted dark:text-nkz-muted">{t('dashboard.plan.robots')}</div>
            <Bot className="w-4 h-4 text-nkz-muted dark:text-nkz-muted" />
          </div>
          <div className="text-sm text-gray-600 dark:text-gray-300 mb-2">
            {t('dashboard.plan.in_use_of_limit', { count: robotsInUse, limit: robotsLimitFrag })}
          </div>
          <ProgressBar
            value={robotsInUse}
            max={limits?.maxRobots ?? undefined}
            label={t('dashboard.plan.robots_usage')}
            barClassName="bg-gradient-to-r from-blue-500 to-blue-600"
          />
        </div>

        <div className="rounded-xl border border-nkz-border dark:border-gray-700 p-4 bg-nkz-bg-secondary dark:bg-gray-700/50">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-nkz-muted dark:text-nkz-muted">{t('dashboard.plan.sensors')}</div>
            <Gauge className="w-4 h-4 text-nkz-muted dark:text-nkz-muted" />
          </div>
          <div className="text-sm text-gray-600 dark:text-gray-300 mb-2">
            {t('dashboard.plan.in_use_of_limit', { count: sensorsInUse, limit: sensorsLimitFrag })}
          </div>
          <ProgressBar
            value={sensorsInUse}
            max={limits?.maxSensors ?? undefined}
            label={t('dashboard.plan.sensors_usage')}
            barClassName="bg-gradient-to-r from-green-500 to-green-600"
          />
        </div>

        <div className="rounded-xl border border-nkz-border dark:border-gray-700 p-4 bg-nkz-bg-secondary dark:bg-gray-700/50">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-nkz-muted dark:text-nkz-muted">{t('dashboard.plan.monitored_area')}</div>
            <MapPin className="w-4 h-4 text-nkz-muted dark:text-nkz-muted" />
          </div>
          <div className="text-sm text-gray-600 dark:text-gray-300 mb-2">
            {t('dashboard.plan.area_in_use', { value: areaInUse.toFixed(2), limit: areaLimitFrag })}
          </div>
          <ProgressBar
            value={areaInUse}
            max={limits?.maxAreaHectares ?? undefined}
            label={t('dashboard.plan.area_coverage')}
            barClassName="bg-gradient-to-r from-amber-500 to-amber-600"
          />
        </div>
      </div>

      <div className="px-6 pb-5 text-xs text-nkz-muted dark:text-nkz-muted flex items-center justify-between">
        <div className="flex items-center gap-2">
          <RefreshCw className="w-3 h-3" />
          <span>
            {t('dashboard.plan.updated', { time: updatedAt ? new Date(updatedAt).toLocaleString('es-ES') : 'recientemente' })}
          </span>
        </div>
        <span>{t('dashboard.plan.plan_label', { plan: planName })}</span>
      </div>
    </div>
  );
};

export default PlanSummaryCard;
