// =============================================================================
// NotificationBell — campana global de avisos (entidad Alert, módulo risk)
// =============================================================================
// Muestra un contador de avisos activos (high/critical) y un desplegable con la
// lista, para que el usuario vea los riesgos sin entrar al módulo. Superficie
// complementada por el panel de detalle de parcela (ParcelDetailsPanel).
import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bell } from 'lucide-react';
import api, { AlertEntity } from '@/services/api';
import { useI18n } from '@/context/I18nContext';
import { useModules } from '@/context/ModuleContext';

const SEV: Record<string, string> = {
  critical: 'bg-nkz-danger-soft text-nkz-danger-strong',
  high: 'bg-nkz-warning-soft text-nkz-warning-strong',
  medium: 'bg-nkz-info-soft text-nkz-info-strong',
  low: 'text-nkz-text-muted',
};

const shortId = (urn?: string): string => {
  if (!urn) return '';
  return urn.split(':').pop() ?? urn;
};

export const NotificationBell: React.FC = () => {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { modules } = useModules();
  const [alerts, setAlerts] = useState<AlertEntity[]>([]);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const riskRoute = (Array.isArray(modules) ? modules : []).find((m) => m?.id === 'risk')?.routePath;

  const load = async () => {
    setAlerts(await api.getActiveAlerts({ limit: 100 }));
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, []);

  const important = alerts.filter((a) => a.severity === 'high' || a.severity === 'critical');
  const count = important.length;

  const openRisk = () => {
    setOpen(false);
    if (riskRoute) navigate(riskRoute);
  };

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label={t('alerts.title')}
        className="relative flex items-center justify-center w-9 h-9 rounded-lg text-nkz-muted hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
      >
        <Bell className="w-5 h-5" />
        {count > 0 && (
          <span className="absolute -top-1 -right-1 flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 rounded-full bg-nkz-danger-strong text-white text-[10px] font-semibold">
            {count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-80 max-h-96 overflow-y-auto rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-xl z-50">
          <div className="flex items-center justify-between px-3 py-2 border-b border-slate-100 dark:border-slate-800">
            <span className="text-sm font-medium text-nkz-text-primary">{t('alerts.title')}</span>
            {riskRoute && (
              <button onClick={openRisk} className="text-xs text-nkz-accent-base hover:underline">
                {t('alerts.viewAll')}
              </button>
            )}
          </div>
          {alerts.length === 0 ? (
            <div className="px-3 py-6 text-sm text-nkz-muted text-center">{t('alerts.empty')}</div>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {alerts.map((a) => (
                <li key={a.id} className="px-3 py-2 flex items-start gap-2">
                  <span className={`mt-0.5 rounded-full px-2 py-0.5 text-[10px] font-medium ${SEV[a.severity ?? 'low'] ?? SEV.low}`}>
                    {a.severity ?? 'low'}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="text-sm font-medium text-nkz-text-primary truncate">{a.alertType ?? '—'}</span>
                      <span className="text-xs text-nkz-muted">{a.category}</span>
                    </div>
                    {a.refEntity && (
                      <div className="text-xs text-nkz-muted truncate">Parcela {shortId(a.refEntity)}</div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};
