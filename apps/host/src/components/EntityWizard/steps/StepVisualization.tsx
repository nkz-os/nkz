import { MapPin } from 'lucide-react';
import { useWizard } from '../WizardContext';
import { IconUploader } from '../IconUploader';
import { AssetBrowser } from '../AssetBrowser';
import { DefaultIconSelector } from '../DefaultIconSelector';
import { useViewer } from '@/context/ViewerContext';
import type { Geometry, Point } from 'geojson';
import { useNotification } from '@/hooks/useNotification';
import { Button } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';

export function StepVisualization() {
  const { showNotification } = useNotification();
  const { entityType, formData, updateFormData } = useWizard();
  const { startModelPreview } = useViewer();
  const { t } = useI18n();

  if (!formData) return null;

  const getPointCoords = (geometry: Geometry | null) => {
    if (!geometry || geometry.type !== 'Point') return null;
    const [lon, lat] = (geometry as Point).coordinates;
    return { lat, lon };
  };

  const coords = getPointCoords(formData.geometry);

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold mb-1">{t('wizard.visualization.title')}</h3>
        <p className="text-sm text-gray-600">{t('wizard.visualization.subtitle')}</p>
      </div>

      {/* Default icon */}
      <div className="bg-nkz-bg-secondary rounded-xl p-4">
        <DefaultIconSelector
          entityType={entityType ?? undefined}
          selectedIcon={formData.defaultIconKey ?? null}
          onSelect={iconKey => updateFormData({
            defaultIconKey: iconKey ?? undefined,
            iconUrl: iconKey ? undefined : formData.iconUrl,
          })}
        />
      </div>

      {/* Divider */}
      <div className="relative flex items-center">
        <div className="flex-1 border-t border-nkz-border" />
        <span className="px-3 text-sm text-nkz-muted bg-white">{t('wizard.visualization.or_upload_icon')}</span>
        <div className="flex-1 border-t border-nkz-border" />
      </div>

      {/* Custom icon upload */}
      <IconUploader
        currentIconUrl={formData.iconUrl}
        onUpload={url => updateFormData({ iconUrl: url, defaultIconKey: undefined })}
        onRemove={() => updateFormData({ iconUrl: undefined })}
      />

      {/* 3D Model */}
      <div className="border-t pt-6">
        <h4 className="text-md font-medium text-gray-900 mb-4">{t('wizard.visualization.model_3d_optional')}</h4>

        <AssetBrowser
          selectedUrl={formData.model3DUrl}
          onSelect={url => updateFormData({ model3DUrl: url })}
          scale={formData.modelScale ?? 1.0}
          onScaleChange={s => updateFormData({ modelScale: s })}
          rotation={formData.modelRotation ?? [0, 0, 0]}
          onRotationChange={r => updateFormData({ modelRotation: r })}
        />

        {formData.model3DUrl && (
          <div className="mt-4 bg-gradient-to-br from-blue-50 to-indigo-50 p-4 rounded-xl border border-blue-100">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-gray-800">{t('wizard.visualization.visual_adjust')}</span>
              <span className="text-xs text-nkz-muted">
                {t('wizard.visualization.scale_rotation', { scale: (formData.modelScale ?? 1).toFixed(1), rotation: formData.modelRotation?.[0] ?? 0 })}
              </span>
            </div>
            <Button
              type="button"
              onClick={() => {
                if (!coords) {
                  showNotification({ type: 'error', message: t('wizard.visualization.location_required') });
                  return;
                }
                startModelPreview(formData.model3DUrl!, coords, {
                  scale: formData.modelScale ?? 1,
                  rotation: formData.modelRotation ?? [0, 0, 0],
                });
              }}
              className="w-full flex items-center justify-center gap-3 px-4 py-3 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-lg hover:from-blue-700 hover:to-indigo-700 font-medium shadow-md"
            >
              <MapPin className="w-5 h-5" />
              {t('wizard.visualization.preview_on_terrain')}
            </Button>
            <p className="text-xs text-nkz-muted text-center mt-2">{t('wizard.visualization.preview_hint')}</p>
          </div>
        )}
      </div>
    </div>
  );
}
