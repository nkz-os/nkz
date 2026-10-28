import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Input, Select } from '@nekazari/ui-kit';
import { useWizard } from '../WizardContext';
import type { IoTSensorFormData } from '../types';
import { useAuth } from '@/context/KeycloakAuthContext';
import {
  DATALOGGER_INSTALLER_ROLES,
  DATALOGGER_KINDS,
  listDataloggers,
  type Datalogger,
  type DataloggerKind,
} from '@/services/dataloggersApi';
import { EXTERNAL_ID_RE, slugifyExternalId } from '@/utils/deviceIds';

/** "Where does the data arrive from?" — an existing datalogger or a new one (Device only). */
export function StepDatalogger() {
  const { t } = useTranslation();
  const { formData, updateFormData } = useWizard();
  const canInstall = useAuth().hasAnyRole(DATALOGGER_INSTALLER_ROLES);
  const [dataloggers, setDataloggers] = useState<Datalogger[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const data = formData?.macroCategory === 'sensors' ? (formData as IoTSensorFormData) : null;

  useEffect(() => {
    let active = true;
    listDataloggers()
      .then((list) => active && setDataloggers(list.filter((d) => d.status === 'active')))
      .catch(() => {
        if (!active) return;
        setDataloggers([]);
        setLoadFailed(true);
      });
    return () => {
      active = false;
    };
  }, []);

  // With no datalogger to pick, the only choice is a new one.
  useEffect(() => {
    if (dataloggers && dataloggers.length === 0 && data?.dataloggerMode !== 'new') {
      updateFormData({ dataloggerMode: 'new' });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataloggers]);

  // Suggest the topic id from the sensor name until the user types their own.
  const name = data?.name ?? '';
  const hasExternalId = Boolean(data?.externalId);
  useEffect(() => {
    if (!hasExternalId && name) updateFormData({ externalId: slugifyExternalId(name) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, hasExternalId]);

  if (!data) return null;
  if (!canInstall) {
    return (
      <div className="space-y-4">
        <h3 className="text-lg font-semibold">{t('wizard.datalogger.title')}</h3>
        <p className="text-sm text-nkz-text-secondary">{t('wizard.datalogger.role_required')}</p>
      </div>
    );
  }
  const mode = data.dataloggerMode ?? 'existing';
  const externalId = data.externalId ?? '';
  const externalIdInvalid = externalId !== '' && !EXTERNAL_ID_RE.test(externalId);
  const hasExisting = (dataloggers?.length ?? 0) > 0;

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">{t('wizard.datalogger.title')}</h3>
      <p className="text-sm text-nkz-text-secondary">{t('wizard.datalogger.hint')}</p>

      {loadFailed && (
        <div className="bg-nkz-warning-soft border border-nkz-warning/30 text-nkz-warning-strong px-4 py-3 rounded-lg text-sm">
          {t('wizard.datalogger.load_failed')}
        </div>
      )}

      <div className="flex gap-2">
        <Button
          type="button"
          variant={mode === 'existing' ? 'primary' : 'secondary'}
          disabled={!hasExisting}
          onClick={() => updateFormData({ dataloggerMode: 'existing' })}
        >
          {t('wizard.datalogger.use_existing')}
        </Button>
        <Button
          type="button"
          variant={mode === 'new' ? 'primary' : 'secondary'}
          onClick={() => updateFormData({ dataloggerMode: 'new' })}
        >
          {t('wizard.datalogger.create_new')}
        </Button>
      </div>

      {mode === 'existing' ? (
        <div>
          <label className="block text-sm font-medium text-nkz-text-primary mb-1">
            {t('wizard.datalogger.select_label')}
          </label>
          <Select
            value={data.dataloggerId ?? ''}
            onValueChange={(dataloggerId) => updateFormData({ dataloggerId })}
            placeholder={t('wizard.datalogger.select_placeholder')}
            options={(dataloggers ?? []).map((d) => ({
              value: d.id,
              label: `${d.name} (${t(`dataloggers.kind.${d.kind}`)})`,
            }))}
          />
          <p className="text-xs text-nkz-text-muted mt-1">{t('wizard.datalogger.existing_hint')}</p>
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-nkz-text-primary mb-1">
              {t('wizard.datalogger.name_label')}
            </label>
            <Input
              type="text"
              value={data.newDataloggerName ?? ''}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => updateFormData({ newDataloggerName: e.target.value })}
              placeholder={t('wizard.datalogger.name_placeholder')}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-nkz-text-primary mb-1">
              {t('wizard.datalogger.kind_label')}
            </label>
            <Select
              value={data.newDataloggerKind ?? 'datak'}
              onValueChange={(kind) => updateFormData({ newDataloggerKind: kind as DataloggerKind })}
              options={DATALOGGER_KINDS.map((kind) => ({ value: kind, label: t(`dataloggers.kind.${kind}`) }))}
            />
          </div>
          <p className="text-xs text-nkz-text-muted">{t('wizard.datalogger.new_hint')}</p>
        </div>
      )}

      <div>
        <label className="block text-sm font-medium text-nkz-text-primary mb-1">
          {t('wizard.datalogger.external_id_label')}
        </label>
        <Input
          type="text"
          value={externalId}
          maxLength={64}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => updateFormData({ externalId: e.target.value })}
          placeholder={t('wizard.datalogger.external_id_placeholder')}
        />
        <p className={`text-xs mt-1 ${externalIdInvalid ? 'text-nkz-danger-strong' : 'text-nkz-text-muted'}`}>
          {t('wizard.datalogger.external_id_hint')}
        </p>
      </div>
    </div>
  );
}
