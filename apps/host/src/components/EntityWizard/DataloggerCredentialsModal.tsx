/**
 * DataloggerCredentialsModal — the one-time view of a datalogger's MQTT account.
 *
 * SECURITY: the password lives only in the `credentials` prop. The caller must
 * drop it (set its state to null) when `onClose` fires. This component never
 * logs it, never stores it and never puts it in a file: the downloadable JSON is
 * the `/config` answer of the API, which carries no password.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Check, Copy, FileDown, X } from 'lucide-react';
import { Button, Input } from '@nekazari/ui-kit';
import { getDataloggerConfig, type MqttEndpoint } from '@/services/dataloggersApi';

export interface DataloggerCredentialsModalProps {
  isOpen: boolean;
  onClose: () => void;
  datalogger: { id: string; name: string };
  /** `configured: false` shows `reason` and no account data. */
  credentials: MqttEndpoint;
  /** Topic of the sensor just registered, when the modal follows a registration. */
  topic?: string;
  /** Extra notice shown above the credentials. */
  hint?: string;
}

export function DataloggerCredentialsModal({
  isOpen,
  onClose,
  datalogger,
  credentials,
  topic,
  hint,
}: DataloggerCredentialsModalProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState<string | null>(null);
  const [downloadFailed, setDownloadFailed] = useState(false);

  if (!isOpen) return null;
  const configured = credentials.configured !== false;

  const copy = (value: string, field: string) => {
    void navigator.clipboard.writeText(value);
    setCopied(field);
    setTimeout(() => setCopied(null), 2000);
  };

  const download = async () => {
    setDownloadFailed(false);
    try {
      const config = await getDataloggerConfig(datalogger.id);
      const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `datalogger-${datalogger.id}.json`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch {
      setDownloadFailed(true);
    }
  };

  const field = (label: string, value: string, key: string, secret = false) => (
    <div className="space-y-1">
      <label className="block text-xs font-medium text-nkz-text-primary">{label}</label>
      <div className="flex items-center gap-2">
        <Input
          type={secret ? 'password' : 'text'}
          value={value}
          readOnly
          aria-label={label}
          className="font-mono"
        />
        <Button type="button" variant="ghost" onClick={() => copy(value, key)} title={t('wizard.mqtt.copy')}>
          {copied === key ? <Check className="w-4 h-4 text-nkz-success-strong" /> : <Copy className="w-4 h-4" />}
        </Button>
      </div>
    </div>
  );

  return (
    <div className="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center z-[110] p-4">
      <div
        role="dialog"
        aria-label={t('dataloggers.credentials.title')}
        className="bg-nkz-surface text-nkz-text-primary rounded-2xl shadow-2xl max-w-xl w-full max-h-[90vh] overflow-y-auto"
      >
        <div className="px-6 py-5 flex justify-between items-start border-b border-nkz-border">
          <div>
            <h2 className="text-xl font-bold text-nkz-text-primary">{t('dataloggers.credentials.title')}</h2>
            <p className="text-sm text-nkz-text-secondary">{datalogger.name}</p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClose} title={t('close')}>
            <X className="w-5 h-5" />
          </Button>
        </div>

        <div className="p-6 space-y-4">
          {configured ? (
            <>
              <div className="flex items-center gap-2 bg-nkz-warning-soft border border-nkz-warning/30 rounded-lg px-4 py-3">
                <AlertTriangle className="w-5 h-5 text-nkz-warning-strong shrink-0" />
                <p className="text-sm text-nkz-warning-strong">{t('dataloggers.credentials.warning_once')}</p>
              </div>
              {hint && <p className="text-sm text-nkz-warning-strong">{hint}</p>}
              {credentials.host && field(t('dataloggers.credentials.host'), credentials.host, 'host')}
              {credentials.port !== undefined && field(t('dataloggers.credentials.port'), String(credentials.port), 'port')}
              {field(t('dataloggers.credentials.username'), credentials.username ?? '', 'username')}
              {field(t('dataloggers.credentials.password'), credentials.password ?? '', 'password', true)}
              {topic && field(t('dataloggers.credentials.topic'), topic, 'topic')}
            </>
          ) : (
            <div className="bg-nkz-warning-soft border border-nkz-warning/30 rounded-lg px-4 py-3 space-y-2">
              <p className="text-sm font-medium text-nkz-warning-strong">
                {t('dataloggers.credentials.not_configured')}
              </p>
              {credentials.reason && <p className="text-sm text-nkz-warning-strong">{credentials.reason}</p>}
              <p className="text-sm text-nkz-text-secondary">{t('dataloggers.credentials.not_configured_next')}</p>
            </div>
          )}

          <Button type="button" variant="secondary" onClick={() => void download()}>
            <FileDown className="w-4 h-4 mr-2" />
            {t('dataloggers.credentials.download')}
          </Button>
          {configured && <p className="text-xs text-nkz-text-muted">{t('dataloggers.credentials.download_hint')}</p>}
          {downloadFailed && (
            <p className="text-sm text-nkz-danger-strong">{t('dataloggers.credentials.download_failed')}</p>
          )}
        </div>

        <div className="px-6 py-4 border-t border-nkz-border">
          <Button type="button" variant="primary" className="w-full" onClick={onClose}>
            {t('dataloggers.credentials.saved')}
          </Button>
        </div>
      </div>
    </div>
  );
}
