import { useEffect, useState } from 'react';
import type { PlacementState } from '@/machines/placementMachine';
import { useI18n } from '@/context/I18nContext';
import type { GeoAssetFormData } from '../types';
import { previewPlants, type PlantsPreview } from '../submission/plants';

interface Props {
  placementState: PlacementState;
  formData: GeoAssetFormData;
}

/** What creating the plants will do: new, updated, left out (with the reason) and warnings. */
export function PlantPreview({ placementState, formData }: Props) {
  const { t } = useI18n();
  const [preview, setPreview] = useState<PlantsPreview | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    previewPlants(placementState, formData)
      .then((p) => { if (!cancelled) setPreview(p); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [placementState, formData]);

  if (failed) return <p className="text-sm text-nkz-danger" role="alert">{t('wizard.plants.preview_failed')}</p>;
  if (!preview) return <p className="text-sm text-nkz-text-secondary">{t('wizard.plants.previewing')}</p>;

  const created = preview.accepted.filter((a) => a.action === 'create').length;
  const updated = preview.accepted.length - created;
  return (
    <div className="space-y-2 text-sm" data-testid="plant-preview">
      <p className="text-nkz-text-primary">{t('wizard.plants.preview_counts', { created, updated, rejected: preview.rejected.length })}</p>
      {preview.rejected.length > 0 && (
        <ul className="text-xs text-nkz-danger space-y-0.5" data-testid="plant-rejected">
          {preview.rejected.map((r, i) => (
            <li key={i}>{t('wizard.plants.rejected_item', {
              code: r.plantCode ?? (r.index !== null ? `#${r.index + 1}` : '—'),
              reason: t(`wizard.plants.reasons.${r.reason}`, { field: r.field ?? '', distance: r.distance_m ?? '' }),
            })}</li>
          ))}
        </ul>
      )}
      {preview.warnings.length > 0 && (
        <ul className="text-xs text-nkz-text-secondary space-y-0.5" data-testid="plant-warnings">
          {preview.warnings.map((w, i) => (
            <li key={i}>{t(`wizard.plants.warnings.${w.code}`, { code: w.plantCode, other: w.other ?? '', distance: w.distance_m ?? '' })}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
