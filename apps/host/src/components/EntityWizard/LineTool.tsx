import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Input } from '@nekazari/ui-kit';
import { useViewer } from '@/context/ViewerContext';
import { useI18n } from '@/context/I18nContext';
import type { PlacementState } from '@/machines/placementMachine';
import { generateLine, haversineM } from '@/utils/generateLine';

type Pt = { lat: number; lon: number };

interface LineToolProps {
  onInstancesChange: (instances: PlacementState['stampedInstances']) => void;
  disabled?: boolean;
}

const DEFAULT_SPACING_M = 1.5;

/** A row of plants: start and end on the map, one plant every `spacing` metres (position 1..n). */
export const LineTool: React.FC<LineToolProps> = ({ onInstancesChange, disabled = false }) => {
  const { t } = useI18n();
  const { pickLocation, setStampInstances } = useViewer();
  const [start, setStart] = useState<Pt | null>(null);
  const [end, setEnd] = useState<Pt | null>(null);
  const [spacing, setSpacing] = useState(String(DEFAULT_SPACING_M));

  const points = useMemo(() => {
    const s = Number(spacing);
    return start && end && s > 0 ? generateLine(start, end, s) : [];
  }, [start, end, spacing]);

  const onChangeRef = useRef(onInstancesChange);
  onChangeRef.current = onInstancesChange;
  useEffect(() => {
    setStampInstances?.(points.map((p) => ({ lat: p.lat, lon: p.lon, height: 0, scale: 1, rotation: 0, col: p.position })));
    onChangeRef.current(points.map((p) => ({ lat: p.lat, lng: p.lon, height: 0, scale: 1, rotation: 0, col: p.position })));
  }, [points, setStampInstances]);

  const pick = (set: (p: Pt) => void) => () => pickLocation((lat: number, lon: number) => set({ lat, lon }));

  return (
    <div className="space-y-3 bg-nkz-surface-sunken border border-nkz-border rounded-xl p-4" data-testid="line-tool">
      <p className="text-sm text-nkz-text-secondary">{t('wizard.line.intro')}</p>
      <div className="flex flex-wrap gap-2">
        <Button variant={start ? 'secondary' : 'primary'} size="sm" disabled={disabled} onClick={pick(setStart)}>
          {start ? t('wizard.line.start_set') : t('wizard.line.pick_start')}
        </Button>
        <Button variant={end ? 'secondary' : 'primary'} size="sm" disabled={disabled || !start} onClick={pick(setEnd)}>
          {end ? t('wizard.line.end_set') : t('wizard.line.pick_end')}
        </Button>
      </div>
      <div>
        <label htmlFor="line-spacing" className="block text-sm font-medium text-nkz-text-primary mb-1">
          {t('wizard.line.spacing')}
        </label>
        <Input id="line-spacing" type="number" min={0.1} step={0.1} value={spacing} disabled={disabled}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSpacing(e.target.value)} />
      </div>
      {start && end && (
        <p className="text-sm text-nkz-text-primary" data-testid="line-summary">
          {t('wizard.line.summary', { count: points.length, length: Math.round(haversineM(start, end) * 10) / 10 })}
        </p>
      )}
    </div>
  );
};
