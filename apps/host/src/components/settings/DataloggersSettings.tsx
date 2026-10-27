/**
 * Settings → Dataloggers: the MQTT accounts that sensors publish through.
 *
 * A new password is shown once, in DataloggerCredentialsModal, and dropped from
 * state when that modal closes.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@nekazari/ui-kit';
import { useConfirm } from '@/context/ConfirmContext';
import { useToastContext } from '@/context/ToastContext';
import { DataloggerCredentialsModal } from '@/components/EntityWizard/DataloggerCredentialsModal';
import {
  apiErrorMessage,
  deleteDatalogger,
  enableDatalogger,
  getDataloggerConfig,
  listDataloggers,
  revokeDatalogger,
  rotateDatalogger,
  type Datalogger,
  type MqttEndpoint,
} from '@/services/dataloggersApi';

interface OneTimeCredentials {
  datalogger: { id: string; name: string };
  credentials: MqttEndpoint;
}

export function DataloggersSettings() {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const toast = useToastContext();
  const [items, setItems] = useState<Datalogger[] | null>(null);
  // '' = failed with no server text (the generic message is shown).
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [oneTime, setOneTime] = useState<OneTimeCredentials | null>(null);

  const reload = useCallback(async () => {
    try {
      setItems(await listDataloggers());
      setLoadError(null);
    } catch (err) {
      setItems([]);
      setLoadError(apiErrorMessage(err, ''));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const run = async (id: string, action: () => Promise<void>, failure: string) => {
    setBusyId(id);
    try {
      await action();
    } catch (err) {
      toast.error(apiErrorMessage(err, failure));
    } finally {
      setBusyId(null);
    }
  };

  const onRotate = async (d: Datalogger) => {
    const ok = await confirm({
      title: t('dataloggers.rotate.title'),
      message: t('dataloggers.rotate.confirm', { name: d.name }),
      confirmLabel: t('dataloggers.rotate.action'),
      cancelLabel: t('cancel'),
      tone: 'danger',
    });
    if (!ok) return;
    await run(d.id, async () => {
      const { password } = await rotateDatalogger(d.id);
      const config = await getDataloggerConfig(d.id);
      // Host and port come from /config (it never carries the password).
      setOneTime({
        datalogger: { id: d.id, name: d.name },
        credentials: config.mqtt.configured === false
          ? { configured: false, reason: config.mqtt.reason, username: d.username }
          : { ...config.mqtt, password },
      });
    }, t('dataloggers.errors.rotate_failed'));
  };

  const onToggle = (d: Datalogger) =>
    run(d.id, async () => {
      if (d.status === 'active') await revokeDatalogger(d.id);
      else await enableDatalogger(d.id);
      await reload();
    }, t('dataloggers.errors.status_failed'));

  const onDelete = async (d: Datalogger) => {
    const ok = await confirm({
      title: t('dataloggers.delete.title'),
      message: t('dataloggers.delete.confirm', { name: d.name }),
      confirmLabel: t('dataloggers.delete.action'),
      cancelLabel: t('cancel'),
      tone: 'danger',
    });
    if (!ok) return;
    await run(d.id, async () => {
      await deleteDatalogger(d.id);
      await reload();
    }, t('dataloggers.errors.delete_failed'));
  };

  return (
    <div className="bg-nkz-surface rounded-lg shadow-sm border border-nkz-border p-6">
      <h2 className="text-lg font-semibold text-nkz-text-primary mb-1">{t('dataloggers.title')}</h2>
      <p className="text-sm text-nkz-text-secondary mb-4">{t('dataloggers.description')}</p>

      {loadError !== null && (
        <p className="text-sm text-nkz-danger-strong mb-3">{loadError || t('dataloggers.errors.load_failed')}</p>
      )}
      {items === null && <p className="text-sm text-nkz-text-muted">{t('loading')}</p>}
      {items !== null && items.length === 0 && loadError === null && (
        <p className="text-sm text-nkz-text-muted">{t('dataloggers.empty')}</p>
      )}

      <ul className="space-y-3">
        {(items ?? []).map((d) => {
          const hasDevices = d.devices.length > 0;
          const busy = busyId === d.id;
          return (
            <li key={d.id} className="border border-nkz-border rounded-lg p-4 space-y-2" data-testid={`datalogger-${d.id}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-nkz-text-primary">{d.name}</span>
                <span className="text-xs px-2 py-0.5 rounded-full bg-nkz-surface-sunken text-nkz-text-secondary">
                  {t(`dataloggers.kind.${d.kind}`)}
                </span>
                <span
                  className={`text-xs px-2 py-0.5 rounded-full ${
                    d.status === 'active'
                      ? 'bg-nkz-success-soft text-nkz-success-strong'
                      : 'bg-nkz-danger-soft text-nkz-danger-strong'
                  }`}
                >
                  {t(`dataloggers.status.${d.status}`)}
                </span>
                <span className="text-xs text-nkz-text-muted">
                  {t('dataloggers.device_count', { count: d.devices.length })}
                </span>
              </div>
              {hasDevices && (
                <p className="text-sm text-nkz-text-secondary font-mono break-all">
                  {d.devices.map((dev) => dev.externalId).join(', ')}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={() => void onRotate(d)}>
                  {t('dataloggers.rotate.action')}
                </Button>
                <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={() => void onToggle(d)}>
                  {d.status === 'active' ? t('dataloggers.revoke') : t('dataloggers.enable')}
                </Button>
                <Button
                  type="button"
                  variant="danger"
                  size="sm"
                  disabled={busy || hasDevices}
                  title={hasDevices ? t('dataloggers.delete.blocked') : undefined}
                  onClick={() => void onDelete(d)}
                >
                  {t('dataloggers.delete.action')}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>

      {oneTime && (
        <DataloggerCredentialsModal
          isOpen
          onClose={() => setOneTime(null)}
          datalogger={oneTime.datalogger}
          credentials={oneTime.credentials}
        />
      )}
    </div>
  );
}
