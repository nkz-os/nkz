import { useState, useEffect } from 'react';
import { Sprout } from 'lucide-react';
import { useWizard } from '../WizardContext';
import { ParentEntitySelector } from '../ParentEntitySelector';
import api from '@/services/api';
import type { GeoAssetFormData } from '../types';
import { Input } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';

// Types that support the subdivision (parent-child) workflow
/* eslint-disable @typescript-eslint/no-explicit-any */
const SUBDIVISION_CAPABLE = new Set([
  'AgriParcel', 'Vineyard', 'OliveGrove', 'AgriBuilding',
]);

// Types that should pick an associated parcel (for module integration)
const PARCEL_ASSOCIATION_TYPES = new Set([
  'AgriEnergyTracker', 'PhotovoltaicInstallation',
]);

// AgriParcel gets first-class cadastral fields instead of generic additionalAttributes
const IS_AGRI_PARCEL = (type: string) => type === 'AgriParcel';
const IS_GREENHOUSE = (type: string) => type === 'AgriGreenhouse';

export function StepGeoAssetConfig() {
  const { entityType, formData, updateFormData } = useWizard();
  const { t } = useI18n();

  const needsParcel = PARCEL_ASSOCIATION_TYPES.has(entityType ?? '');
  const [parcels, setParcels] = useState<{ id: string; name: string }[]>([]);
  const [parcelsLoading, setParcelsLoading] = useState(false);

  useEffect(() => {
    if (!needsParcel) return;
    setParcelsLoading(true);
    api.getSDMEntityInstances('AgriParcel')
      .then((entities: any[]) => {
        setParcels(entities.map(e => ({
          id: e.id,
          name: typeof e.name === 'string' ? e.name : e.name?.value || e.id,
        })));
      })
      .catch(() => setParcels([]))
      .finally(() => setParcelsLoading(false));
  }, [needsParcel]);

  if (!formData || formData.macroCategory !== 'assets') return null;
  const data = formData as GeoAssetFormData;

  const canSubdivide = SUBDIVISION_CAPABLE.has(entityType ?? '');
  const isAgriParcel = IS_AGRI_PARCEL(entityType ?? '');
  const isGreenhouse = IS_GREENHOUSE(entityType ?? '');

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">{t('wizard.asset.title')}</h3>

      {/* Name */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">{t('wizard.fields.name_required')}</label>
        <Input
          type="text"
          value={data.name}
          onChange={(e: any) => updateFormData({ name: e.target.value })}
          className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-green-500"
          placeholder={t('wizard.asset.name_placeholder')}
        />
      </div>

      {/* Description */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">{t('wizard.fields.description')}</label>
        <textarea
          value={data.description ?? ''}
          onChange={(e: any) => updateFormData({ description: e.target.value })}
          className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-green-500"
          placeholder={t('wizard.fields.description_placeholder')}
          rows={2}
        />
      </div>

      {/* AgriParcel: first-class cadastral fields */}
      {isAgriParcel && (
        <div className="pt-4 border-t space-y-3">
          <h4 className="text-sm font-medium text-gray-700">{t('wizard.asset.cadastral_data')}</h4>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('wizard.asset.municipality')}</label>
              <Input
                type="text"
                value={data.municipality ?? ''}
                onChange={(e: any) => updateFormData({ municipality: e.target.value })}
                className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-green-500"
                placeholder={t('wizard.asset.municipality_placeholder')}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('wizard.asset.province')}</label>
              <Input
                type="text"
                value={data.province ?? ''}
                onChange={(e: any) => updateFormData({ province: e.target.value })}
                className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-green-500"
                placeholder={t('wizard.asset.province_placeholder')}
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('wizard.asset.cadastral_reference')}</label>
            <Input
              type="text"
              value={data.cadastralReference ?? ''}
              onChange={(e: any) => updateFormData({ cadastralReference: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-green-500 font-mono"
              placeholder={t('wizard.asset.cadastral_reference_placeholder')}
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('wizard.asset.crop_type')}</label>
            <Input
              type="text"
              value={data.cropType ?? ''}
              onChange={(e: any) => updateFormData({ cropType: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              placeholder={t('wizard.asset.crop_type_placeholder')}
            />
          </div>
        </div>
      )}

      {/* Parcel association (AgriEnergyTracker, PhotovoltaicInstallation) */}
      {needsParcel && (
        <div className="pt-4 border-t">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            <span className="flex items-center gap-1.5">
              <Sprout className="w-4 h-4 text-nkz-success" />
              {t('wizard.asset.linked_parcel')}
            </span>
          </label>
          <p className="text-xs text-nkz-muted mb-2">
            {t('wizard.asset.linked_parcel_hint')}
          </p>
          {parcelsLoading ? (
            <p className="text-sm text-nkz-muted">{t('wizard.asset.loading_parcels')}</p>
          ) : parcels.length === 0 ? (
            <p className="text-sm text-amber-600">{t('wizard.asset.no_parcels')}</p>
          ) : (
            <select
              value={data.parentEntity?.id ?? ''}
              onChange={(e: any) => {
                const parcel = parcels.find(p => p.id === e.target.value);
                updateFormData({
                  parentEntity: parcel ? { id: parcel.id, type: 'AgriParcel', name: parcel.name, geometry: null } : null,
                });
              }}
              className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-green-500"
            >
              <option value="">{t('wizard.asset.no_parcel_option')}</option>
              {parcels.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* AgriGreenhouse: specific fields for greenhouse DT module */}
      {isGreenhouse && (
        <div className="pt-4 border-t space-y-3">
          <h4 className="text-sm font-medium text-gray-700">{t('wizard.asset.greenhouse_dimensions')}</h4>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('wizard.asset.area_m2')}</label>
              <Input
                type="number"
                min="0"
                value={data.additionalAttributes.area ?? ''}
                onChange={(e: any) => updateFormData({
                  additionalAttributes: { ...data.additionalAttributes, area: Number(e.target.value) || 0 }
                })}
                className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm"
                placeholder={t('wizard.asset.area_placeholder')}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('wizard.asset.height_m')}</label>
              <Input
                type="number"
                min="0"
                step="0.1"
                value={data.additionalAttributes.height ?? ''}
                onChange={(e: any) => updateFormData({
                  additionalAttributes: { ...data.additionalAttributes, height: Number(e.target.value) || 0 }
                })}
                className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm"
                placeholder={t('wizard.asset.height_placeholder')}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('wizard.asset.cover_type')}</label>
              <select
                value={data.additionalAttributes.coverType as string ?? ''}
                onChange={(e: any) => updateFormData({
                  additionalAttributes: { ...data.additionalAttributes, coverType: e.target.value }
                })}
                className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              >
                <option value="">{t('wizard.asset.select_option')}</option>
                <option value="polyethylene">{t('wizard.asset.cover_polyethylene')}</option>
                <option value="glass">{t('wizard.asset.cover_glass')}</option>
                <option value="polycarbonate">{t('wizard.asset.cover_polycarbonate')}</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('wizard.asset.orientation')}</label>
              <select
                value={data.additionalAttributes.orientation as string ?? ''}
                onChange={(e: any) => updateFormData({
                  additionalAttributes: { ...data.additionalAttributes, orientation: e.target.value }
                })}
                className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              >
                <option value="">{t('wizard.asset.select_option')}</option>
                <option value="N-S">{t('wizard.asset.orientation_ns')}</option>
                <option value="E-W">{t('wizard.asset.orientation_ew')}</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {/* Subdivision / parent entity */}
      {canSubdivide && (
        <div className="pt-4 border-t">
          <div className="flex items-center gap-2 mb-3">
            <Input
              type="checkbox"
              id="isSubdivision"
              checked={data.isSubdivision}
              onChange={(e: any) => updateFormData({
                isSubdivision: e.target.checked,
                parentEntity: e.target.checked ? data.parentEntity : null,
              })}
              className="w-4 h-4 accent-green-600"
            />
            <label htmlFor="isSubdivision" className="text-sm font-medium text-gray-700">
              {t('wizard.asset.create_as_subdivision')}
            </label>
          </div>

          {data.isSubdivision && (
            <ParentEntitySelector
              selectedParentId={data.parentEntity?.id}
              onSelect={parent => updateFormData({ parentEntity: parent })}
              entityType={entityType ?? undefined}
            />
          )}
        </div>
      )}
    </div>
  );
}
