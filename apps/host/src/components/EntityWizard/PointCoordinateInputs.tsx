import { useEffect, useState } from 'react';
import type { Geometry, Point } from 'geojson';
import { Input } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';
import { parsePointCoordinates } from './iotValidation';

interface PointCoordinateInputsProps {
  geometry: Geometry | null | undefined;
  /** Called with a valid Point, or null while the typed pair is incomplete or invalid. */
  onChange: (point: Point | null) => void;
}

function toTexts(geometry: Geometry | null | undefined): [string, string] {
  if (geometry?.type !== 'Point') return ['', ''];
  return [String(geometry.coordinates[1]), String(geometry.coordinates[0])];
}

export function PointCoordinateInputs({ geometry, onChange }: PointCoordinateInputsProps) {
  const { t } = useI18n();
  const [latText, setLatText] = useState(() => toTexts(geometry)[0]);
  const [lonText, setLonText] = useState(() => toTexts(geometry)[1]);

  // A map click commits a Point: mirror it into the fields unless they already denote it.
  useEffect(() => {
    if (geometry?.type !== 'Point') return;
    const parsed = parsePointCoordinates(latText, lonText);
    const [lon, lat] = geometry.coordinates;
    if (parsed.status === 'ok' && parsed.point.coordinates[0] === lon && parsed.point.coordinates[1] === lat) return;
    const [nextLat, nextLon] = toTexts(geometry);
    setLatText(nextLat);
    setLonText(nextLon);
  }, [geometry]); // eslint-disable-line react-hooks/exhaustive-deps

  const update = (nextLat: string, nextLon: string) => {
    setLatText(nextLat);
    setLonText(nextLon);
    const parsed = parsePointCoordinates(nextLat, nextLon);
    onChange(parsed.status === 'ok' ? parsed.point : null);
  };

  const invalid = parsePointCoordinates(latText, lonText).status === 'invalid';

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor="wizard-point-lat" className="block text-sm font-medium text-nkz-text-primary mb-1">
            {t('wizard.geometry.latitude')}
          </label>
          <Input
            id="wizard-point-lat"
            type="text"
            inputMode="decimal"
            value={latText}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => update(e.target.value, lonText)}
          />
        </div>
        <div>
          <label htmlFor="wizard-point-lon" className="block text-sm font-medium text-nkz-text-primary mb-1">
            {t('wizard.geometry.longitude')}
          </label>
          <Input
            id="wizard-point-lon"
            type="text"
            inputMode="decimal"
            value={lonText}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => update(latText, e.target.value)}
          />
        </div>
      </div>
      {invalid && (
        <p role="alert" className="text-nkz-danger-strong text-sm font-medium">
          {t('wizard.geometry.coordinates_invalid')}
        </p>
      )}
    </div>
  );
}
