import React from 'react';
import { useI18n } from '@/context/I18nContext';
import { useEntityEditor } from '../EntityEditorContext';
import type { NGSAttribute } from '@/types/ngsi-ld';
import { Input } from '@nekazari/ui-kit';

export const GeometrySection: React.FC = () => {
  const { t } = useI18n();
  const { formState, setField } = useEntityEditor();
  const locationAttr = formState.attributes['location'];
  if (!locationAttr || (locationAttr as any).type !== 'GeoProperty') return null;

  const locValue = (locationAttr as any).value;
  const geomType = locValue?.type || 'Point';
  const coords = locValue?.coordinates;

  const handleCoordsChange = (newCoords: any) => {
    setField('location', {
      type: 'GeoProperty',
      value: { type: geomType, coordinates: newCoords },
    } as NGSAttribute);
  };

  return (
    <details className="border border-nkz-border rounded-lg" open>
      <summary className="px-4 py-3 bg-nkz-bg-secondary cursor-pointer text-sm font-semibold text-gray-700 hover:bg-nkz-bg-secondary rounded-lg">
        {t('editor.section.geometry') || 'Geometría'} — {geomType}
      </summary>
      <div className="p-4">
        <div className="h-[300px] bg-nkz-bg-secondary rounded-lg flex items-center justify-center text-nkz-muted text-sm">
          <div className="text-center">
            <p>{t('editor.geometry.placeholder') || 'Editor de geometría'}</p>
            <p className="text-xs mt-1 text-nkz-muted">
              {t('editor.geometry.hint') || 'Edita las coordenadas manualmente o usa el mapa (próxima iteración)'}
            </p>
          </div>
        </div>
        {coords && geomType === 'Point' && (
          <div className="mt-3 grid grid-cols-2 gap-2">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Longitud</label>
              <Input
                type="number"
                step="0.000001"
                value={coords[0] ?? ''}
                onChange={(e: any) => handleCoordsChange([Number(e.target.value), coords[1]])}
                className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Latitud</label>
              <Input
                type="number"
                step="0.000001"
                value={coords[1] ?? ''}
                onChange={(e: any) => handleCoordsChange([coords[0], Number(e.target.value)])}
                className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
        )}
        {coords && geomType === 'Polygon' && (
          <div className="mt-3">
            <p className="text-xs text-nkz-muted mb-2">
              {t('editor.geometry.polygon_hint') || 'Polígono — edición de vértices en el mapa (próxima iteración).'} {' '}
              {coords[0]?.length || 0} {t('editor.geometry.vertices') || 'vértices'}
            </p>
          </div>
        )}
      </div>
    </details>
  );
};
