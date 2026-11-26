/**
 * What a generic MQTT datalogger publishes so the platform sees it alive.
 * Shared by the Settings row and the one-time credentials modal.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Copy } from 'lucide-react';
import { Button, Input } from '@nekazari/ui-kit';
import type { DataloggerBeacon } from '@/services/dataloggersApi';

/** The message a generic MQTT datalogger publishes every `intervalSeconds` (never a TimeInstant). */
export const BEACON_MESSAGE = '{"uptime": <seconds since start, integer>}';

/** `beacon` null/undefined = the configuration has none yet (the projection is not finished). */
export function BeaconInstructions({ beacon }: { beacon: DataloggerBeacon | null | undefined }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  if (!beacon) {
    return (
      <p className="text-sm text-nkz-text-secondary" data-testid="datalogger-beacon-not-ready">
        {t('dataloggers.beacon.not_ready')}
      </p>
    );
  }

  const copy = () => {
    void navigator.clipboard.writeText(beacon.topic);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-2" data-testid="datalogger-beacon">
      <h3 className="text-sm font-semibold text-nkz-text-primary">{t('dataloggers.beacon.title')}</h3>
      <div className="space-y-1">
        <label className="block text-xs font-medium text-nkz-text-primary">{t('dataloggers.beacon.topic')}</label>
        <div className="flex items-center gap-2">
          <Input type="text" value={beacon.topic} readOnly aria-label={t('dataloggers.beacon.topic')} className="font-mono" />
          <Button type="button" variant="ghost" onClick={copy} title={t('wizard.mqtt.copy')}>
            {copied ? <Check className="w-4 h-4 text-nkz-success-strong" /> : <Copy className="w-4 h-4" />}
          </Button>
        </div>
      </div>
      <p className="text-sm text-nkz-text-secondary">
        {t('dataloggers.beacon.intro', { seconds: beacon.intervalSeconds })}
      </p>
      <pre className="text-xs font-mono bg-nkz-surface-sunken text-nkz-text-primary rounded-lg p-3 overflow-x-auto">
        {BEACON_MESSAGE}
      </pre>
      <p className="text-xs text-nkz-text-secondary">{t('dataloggers.beacon.uptime_hint')}</p>
      <p className="text-xs text-nkz-text-secondary">{t('dataloggers.beacon.optional')}</p>
      <p className="text-xs text-nkz-warning-strong">{t('dataloggers.beacon.no_time_instant')}</p>
    </div>
  );
}
