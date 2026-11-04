import { useEffect, useState } from 'react';
import { useWizard } from '../WizardContext';
import api from '@/services/api';
import type { GeoAssetFormData } from '../types';
import { Input } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';
import { PlantFileImport } from '../PlantFileImport';

const PLANT_STATUS = ['alive', 'dead', 'removed', 'replaced'] as const;

/* eslint-disable @typescript-eslint/no-explicit-any */
/** Data step for individual plants (AgriPlant): the parcel is required, the rest is per batch. */
export function StepPlantConfig() {
  const { formData, updateFormData } = useWizard();
  const { t } = useI18n();
  const [parcels, setParcels] = useState<{ id: string; name: string }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getSDMEntityInstances('AgriParcel')
      .then((entities: any[]) => setParcels(entities.map((e) => ({
        id: e.id, name: typeof e.name === 'string' ? e.name : e.name?.value || e.id,
      }))))
      .catch(() => setParcels([]))
      .finally(() => setLoading(false));
  }, []);

  if (!formData || formData.macroCategory !== 'assets') return null;
  const data = formData as GeoAssetFormData;
  const attrs = data.additionalAttributes ?? {};
  const setAttr = (key: string, value: string) => {
    const next = { ...attrs };
    if (value === '') delete next[key]; else next[key] = value;
    updateFormData({ additionalAttributes: next });
  };

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">{t('wizard.plants.title')}</h3>
      <p className="text-sm text-nkz-text-secondary">{t('wizard.plants.intro')}</p>

      <div>
        <label htmlFor="plant-parcel" className="block text-sm font-medium text-nkz-text-primary mb-1">
          {t('wizard.plants.parcel')}
        </label>
        <select
          id="plant-parcel"
          className="w-full rounded-lg border border-nkz-border bg-nkz-surface px-3 py-2 text-sm"
          value={data.parentEntity?.id ?? ''}
          disabled={loading}
          onChange={(e) => {
            const p = parcels.find((x) => x.id === e.target.value);
            updateFormData({ parentEntity: p ? { id: p.id, type: 'AgriParcel', name: p.name, geometry: null } : null });
          }}
        >
          <option value="">{loading ? t('wizard.plants.loading') : t('wizard.plants.choose_parcel')}</option>
          {parcels.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor="plant-code" className="block text-sm font-medium text-nkz-text-primary mb-1">
            {t('wizard.plants.code')}
          </label>
          <Input id="plant-code" value={String(attrs.plantCode ?? '')} placeholder={t('wizard.plants.code_auto')}
            onChange={(e: any) => setAttr('plantCode', e.target.value)} />
        </div>
        <div>
          <label htmlFor="plant-row" className="block text-sm font-medium text-nkz-text-primary mb-1">
            {t('wizard.plants.row')}
          </label>
          <Input id="plant-row" type="number" min={1} value={String(attrs.rowNumber ?? '')}
            onChange={(e: any) => setAttr('rowNumber', e.target.value)} />
        </div>
        <div>
          <label htmlFor="plant-variety" className="block text-sm font-medium text-nkz-text-primary mb-1">
            {t('wizard.plants.variety')}
          </label>
          <Input id="plant-variety" value={String(attrs.variety ?? '')}
            onChange={(e: any) => setAttr('variety', e.target.value)} />
        </div>
        <div>
          <label htmlFor="plant-planted" className="block text-sm font-medium text-nkz-text-primary mb-1">
            {t('wizard.plants.date_planted')}
          </label>
          <Input id="plant-planted" type="date" value={String(attrs.datePlanted ?? '')}
            onChange={(e: any) => setAttr('datePlanted', e.target.value)} />
        </div>
        <div className="col-span-2">
          <label htmlFor="plant-status" className="block text-sm font-medium text-nkz-text-primary mb-1">
            {t('wizard.plants.status')}
          </label>
          <select id="plant-status"
            className="w-full rounded-lg border border-nkz-border bg-nkz-surface px-3 py-2 text-sm"
            value={String(attrs.plantStatus ?? '')} onChange={(e) => setAttr('plantStatus', e.target.value)}>
            <option value="">—</option>
            {PLANT_STATUS.map((s) => <option key={s} value={s}>{t(`wizard.plants.status_${s}`)}</option>)}
          </select>
        </div>
      </div>
      <p className="text-xs text-nkz-text-secondary">{t('wizard.plants.hint')}</p>

      {/* Alternative to placing them on the map: import a whole file at once */}
      {data.parentEntity?.type === 'AgriParcel' && <PlantFileImport parcelId={data.parentEntity.id} />}
    </div>
  );
}
