import { Activity, Building2 } from 'lucide-react';
import { useWizard } from '../WizardContext';
import { ENTITY_TYPE_METADATA } from '../entityTypes';
import type { GeoAssetFormData } from '../types';
import { useI18n } from '@/context/I18nContext';

export function StepSummary() {
  const { entityType, formData } = useWizard();
  const { t } = useI18n();

  if (!formData || !entityType) return null;

  const meta = ENTITY_TYPE_METADATA[entityType];
  const Icon = meta?.icon ?? Activity;
  const assetData = formData.macroCategory === 'assets' ? (formData as GeoAssetFormData) : null;

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">{t('wizard.steps.summary')}</h3>

      <div className="bg-nkz-surface-sunken p-5 rounded-xl border border-nkz-success/30 space-y-3">
        {/* Header */}
        <div className="flex items-center gap-3 pb-3 border-b border-nkz-success/30">
          <Icon className="w-8 h-8 text-nkz-success" />
          <div>
            <div className="font-bold text-lg text-nkz-text-primary">{formData.name || t('wizard.summary.unnamed')}</div>
            <div className="text-sm text-nkz-text-secondary">{entityType}</div>
          </div>
        </div>

        {/* Details grid */}
        <div className="grid grid-cols-2 gap-3 text-sm">
          {formData.description && (
            <div className="col-span-2">
              <span className="text-nkz-muted">{t('wizard.summary.description')}</span>
              <span className="ml-2 text-nkz-text-primary">{formData.description}</span>
            </div>
          )}

          {/* Geo asset: hierarchy + cadastral */}
          {assetData?.isSubdivision && assetData.parentEntity && (
            <div>
              <span className="text-nkz-muted">{t('wizard.summary.parent')}</span>
              <span className="ml-2 text-nkz-text-primary">{assetData.parentEntity.name}</span>
            </div>
          )}
          {assetData?.municipality && (
            <div>
              <span className="text-nkz-muted">{t('wizard.summary.municipality')}</span>
              <span className="ml-2 text-nkz-text-primary">{assetData.municipality}</span>
            </div>
          )}
          {assetData?.cadastralReference && (
            <div className="col-span-2">
              <span className="text-nkz-muted">{t('wizard.summary.cadastral_ref')}</span>
              <span className="ml-2 text-nkz-text-primary font-mono text-xs">{assetData.cadastralReference}</span>
            </div>
          )}

          {/* Geometry */}
          {formData.geometry && (
            <div>
              <span className="text-nkz-muted">{t('wizard.summary.geometry')}</span>
              <span className="ml-2 text-nkz-text-primary">{formData.geometry.type}</span>
            </div>
          )}
        </div>

        {/* Visual assets */}
        {(formData.defaultIconKey || formData.iconUrl || formData.model3DUrl) && (
          <div className="flex gap-4 pt-3 border-t border-nkz-success/30">
            {(formData.defaultIconKey || formData.iconUrl) && (
              <div className="flex items-center gap-2 text-sm">
                <div className="w-8 h-8 bg-nkz-surface rounded-lg border border-nkz-border flex items-center justify-center">
                  {formData.iconUrl
                    ? <img src={formData.iconUrl} alt={t('wizard.summary.icon')} className="w-6 h-6 object-contain" />
                    : <Icon className="w-5 h-5 text-nkz-text-secondary" />
                  }
                </div>
                <span className="text-nkz-text-secondary">{formData.iconUrl ? t('wizard.summary.icon_custom') : t('wizard.summary.icon_default')}</span>
              </div>
            )}
            {formData.model3DUrl && (
              <div className="flex items-center gap-2 text-sm">
                <div className="w-8 h-8 bg-nkz-surface rounded-lg border border-nkz-border flex items-center justify-center">
                  <Building2 className="w-5 h-5 text-nkz-text-secondary" />
                </div>
                <span className="text-nkz-text-secondary">{t('wizard.summary.model_3d')}</span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Robot-specific note */}
      {entityType === 'AutonomousMobileRobot' && (
        <div className="p-3 bg-nkz-warning-soft border border-nkz-warning/30 rounded-lg text-sm text-nkz-warning-strong">
          <strong>{t('wizard.summary.note')}</strong> {t('wizard.summary.robot_note_before')} <a href="/connectivity" className="underline font-medium">{t('wizard.summary.robot_note_link')}</a> {t('wizard.summary.robot_note_after')}
        </div>
      )}

      <p className="text-sm text-nkz-text-secondary">{t('wizard.summary.review_hint')}</p>
    </div>
  );
}
