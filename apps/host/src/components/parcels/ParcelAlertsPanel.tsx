// =============================================================================
// ParcelAlertsPanel — avisos activos (entidad Alert) de una parcela concreta
// =============================================================================
// Sección dentro de ParcelDetailsPanel. Lee los Alert del tenant vía el endpoint
// de alertas del módulo risk y filtra por el refEntity de la parcela.
import React, { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import api, { AlertEntity } from '@/services/api';
import { useI18n } from '@/context/I18nContext';

const SEV: Record<string, string> = {
  critical: 'bg-nkz-danger-soft text-nkz-danger-strong',
  high: 'bg-nkz-warning-soft text-nkz-warning-strong',
  medium: 'bg-nkz-info-soft text-nkz-info-strong',
  low: 'text-nkz-text-muted',
};

interface Props {
  parcelId: string;
}

const matchesParcel = (refEntity: string | undefined, parcelId: string): boolean => {
  if (!refEntity) return false;
  if (refEntity === parcelId) return true;
  const short = parcelId.includes(':') ? parcelId.split(':').pop() ?? parcelId : parcelId;
  return refEntity.endsWith(short);
};

export const ParcelAlertsPanel: React.FC<Props> = ({ parcelId }) => {
  const { t } = useI18n();
  const [alerts, setAlerts] = useState<AlertEntity[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const all = await api.getActiveAlerts({ limit: 200 });
      if (!cancelled) {
        setAlerts(all.filter((a) => matchesParcel(a.refEntity, parcelId)));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [parcelId]);

  if (alerts.length === 0) return null;

  return (
    <div className="bg-nkz-danger-soft rounded-lg p-4 border border-nkz-danger-soft">
      <h4 className="text-sm font-semibold text-nkz-text-primary mb-3 flex items-center gap-2">
        <AlertTriangle className="w-4 h-4 text-nkz-danger-strong" />
        {t('alerts.title')}
      </h4>
      <ul className="space-y-2">
        {alerts.map((a) => (
          <li key={a.id} className="flex items-start gap-2 text-sm">
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEV[a.severity ?? 'low'] ?? SEV.low}`}>
              {a.severity ?? 'low'}
            </span>
            <span className="flex-1">
              <span className="font-medium text-nkz-text-primary">{a.alertType ?? '—'}</span>
              <span className="text-nkz-text-muted"> · {a.category}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
};
