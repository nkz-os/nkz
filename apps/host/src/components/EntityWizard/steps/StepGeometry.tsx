import { useCallback, useMemo } from 'react';
import type { PlacementState, PlacementAction } from '@/machines/placementMachine';
import { useWizard } from '../WizardContext';
import { GeometryEditor } from '../GeometryEditor';
import { PointCoordinateInputs } from '../PointCoordinateInputs';
import { StampTool } from '../StampTool';
import { ArrayTool } from '../ArrayTool';
import { PlacementModeSelector } from '../PlacementModeSelector';
import { AssetBrowser } from '../AssetBrowser';
import { validateGeometryWithinParent } from '@/utils/geometryValidation';
import type { Geometry } from 'geojson';
import type { GeoAssetFormData, IoTSensorFormData } from '../types';
import { Button } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';

// ─── Props — placementState lives in shell (UI state, not form payload) ───────

export interface StepGeometryProps {
  placementState: PlacementState;
  dispatchPlacement: React.Dispatch<PlacementAction>;
}

export function StepGeometry({ placementState, dispatchPlacement }: StepGeometryProps) {
  const { entityType, formData, updateFormData, setValidationError } = useWizard();
  const { t } = useI18n();

  // Stable callback for array mode — uses SET_STAMPED_INSTANCES (atomic replace, no CLEAR+ADD race)
  const handleArrayInstancesChange = useCallback((instances: PlacementState['stampedInstances']) => {
    dispatchPlacement({ type: 'SET_STAMPED_INSTANCES', payload: instances });
    setValidationError(instances.length === 0 ? t('wizard.geometry.anchor_required') : null);
  }, [dispatchPlacement, setValidationError, t]);

  // Stable identity: GeometryEditor re-validates and redraws whenever this object changes.
  const subdivisionParent = (formData as GeoAssetFormData | null)?.isSubdivision
    ? (formData as GeoAssetFormData).parentEntity
    : null;
  const devicePrimaryParcel =
    entityType === 'Device' ? (formData as IoTSensorFormData | null)?.parcels?.[0] : undefined;
  const parentGeometry = useMemo(() => {
    if (subdivisionParent) {
      return { id: subdivisionParent.id, name: subdivisionParent.name, geometry: subdivisionParent.geometry };
    }
    return undefined;
  }, [subdivisionParent]);
  // Device: the first parcel is drawn for context only; a station may sit outside the parcels it serves.
  const outlineGeometry = useMemo(
    () =>
      devicePrimaryParcel?.geometry?.type === 'Polygon'
        ? { id: devicePrimaryParcel.id, name: devicePrimaryParcel.name, geometry: devicePrimaryParcel.geometry }
        : undefined,
    [devicePrimaryParcel],
  );

  if (!formData) return null;

  const isAsset = formData.macroCategory === 'assets';
  const assetData = isAsset ? (formData as GeoAssetFormData) : null;

  const handleGeometryChange = (geometry: Geometry | null) => {
    if (!geometry) {
      updateFormData({ geometry: null });
      return;
    }

    // Geo-fencing validation for subdivisions
    if (assetData?.isSubdivision && assetData.parentEntity?.geometry) {
      const result = validateGeometryWithinParent(geometry, assetData.parentEntity.geometry);
      if (!result.valid) {
        setValidationError(result.error ?? t('wizard.geometry.outside_parent'));
        return;
      }
    }

    setValidationError(null);
    updateFormData({ geometry });
  };

  const geometryType = (formData as GeoAssetFormData).geometryType ?? 'Point';

  const isDevice = entityType === 'Device';

  const handleTypedPoint = (point: Geometry | null) => {
    updateFormData({ geometry: point });
  };

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">
        {formData.macroCategory === 'assets' ? t('wizard.geometry.title_asset') : t('wizard.steps.location')}
      </h3>

      {/* Placement mode (assets only — sensors/fleet are always single point) */}
      {isAsset && (
        <PlacementModeSelector
          mode={placementState.mode}
          onChange={mode => dispatchPlacement({ type: 'SET_MODE', payload: mode })}
          entityType={entityType ?? undefined}
        />
      )}

      {/* Stamp mode: asset library + paint tool */}
      {placementState.mode === 'stamp' && (
        <>
          <div className="bg-nkz-surface-sunken border border-nkz-info/30 rounded-xl p-4">
            <h4 className="font-semibold text-nkz-info-strong mb-2">{t('wizard.geometry.select_stamp_asset')}</h4>
            <AssetBrowser
              selectedUrl={formData.model3DUrl}
              onSelect={url => {
                updateFormData({ model3DUrl: url });
                dispatchPlacement({ type: 'SELECT_MODEL', payload: url });
              }}
              scale={formData.modelScale}
              onScaleChange={s => updateFormData({ modelScale: s })}
            />
            {!formData.model3DUrl && (
              <p className="text-nkz-danger-strong text-sm mt-2 font-medium">{t('wizard.geometry.model_required')}</p>
            )}
          </div>
          <StampTool
            modelUrl={formData.model3DUrl}
            onInstancesChange={instances => {
              dispatchPlacement({ type: 'ADD_STAMPED_INSTANCES', payload: instances });
              setValidationError(instances.length === 0 ? t('wizard.validation.stamp_required') : null);
            }}
            height="h-96"
          />
        </>
      )}

      {/* Array mode: asset library + grid tool */}
      {placementState.mode === 'array' && (
        <>
          <div className="bg-nkz-surface-sunken border border-nkz-info/30 rounded-xl p-4">
            <h4 className="font-semibold text-nkz-info-strong mb-2">{t('wizard.geometry.select_array_asset')}</h4>
            <AssetBrowser
              selectedUrl={formData.model3DUrl}
              onSelect={url => {
                updateFormData({ model3DUrl: url });
                dispatchPlacement({ type: 'SELECT_MODEL', payload: url });
              }}
              scale={formData.modelScale}
              onScaleChange={s => updateFormData({ modelScale: s })}
            />
            {!formData.model3DUrl && (
              <p className="text-nkz-danger-strong text-sm mt-2 font-medium">{t('wizard.geometry.model_required')}</p>
            )}
          </div>
          <ArrayTool
            modelUrl={formData.model3DUrl}
            onInstancesChange={handleArrayInstancesChange}
            placementState={placementState}
            dispatchPlacement={dispatchPlacement}
            entityType={entityType ?? undefined}
          />
        </>
      )}

      {/* Normal geometry editor */}
      {placementState.mode !== 'stamp' && placementState.mode !== 'array' && (
        <>
          {/* Geometry type selector (assets only) */}
          {isAsset && (
            <div>
              <label className="block text-sm font-medium text-nkz-text-primary mb-2">{t('wizard.geometry.geometry_type')}</label>
              <div className="grid grid-cols-4 gap-2">
                {(['Point', 'Polygon', 'LineString', 'MultiLineString'] as const).map(gt => (
                  <Button variant="ghost"
                    key={gt}
                    type="button"
                    onClick={() => updateFormData({ geometryType: gt, geometry: null })}
                    className={`p-2 rounded border text-sm ${geometryType === gt ? 'border-green-500 bg-nkz-surface-sunken' : 'border-nkz-border hover:border-nkz-success/30'}`}
                  >
                    {gt}
                  </Button>
                ))}
              </div>
            </div>
          )}

          <GeometryEditor
            geometryType={geometryType}
            parentGeometry={parentGeometry}
            outlineGeometry={outlineGeometry}
            initialGeometry={formData.geometry ?? undefined}
            onGeometryChange={handleGeometryChange}
            onValidationChange={(valid, err) => setValidationError(valid ? null : (err ?? null))}
            height="h-96"
          />

          {isDevice && (
            <PointCoordinateInputs geometry={formData.geometry} onChange={handleTypedPoint} />
          )}

          {assetData?.isSubdivision && assetData.parentEntity && (
            <div className="p-3 bg-nkz-warning-soft border border-nkz-warning/30 rounded-lg text-sm text-nkz-warning-strong">
              <strong>{t('wizard.summary.parent')}</strong> {assetData.parentEntity.name} ({assetData.parentEntity.type})<br />
              <span className="text-xs text-nkz-warning-strong">{t('wizard.geometry.inside_parent_hint')}</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}
