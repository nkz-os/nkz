import { fireEvent, render, screen } from '@testing-library/react';
import destination from '@turf/destination';
import { point } from '@turf/helpers';
import { describe, expect, it, vi } from 'vitest';

const picks: Array<[number, number]> = [];
const [endLon, endLat] = destination(point([-1.6, 42.8]), 0.006, 90).geometry.coordinates;
picks.push([42.8, -1.6], [endLat, endLon]);
const setStampInstances = vi.fn();
vi.mock('@/context/ViewerContext', () => ({
  useViewer: () => ({
    pickLocation: (cb: (lat: number, lon: number) => void) => { const p = picks.shift()!; cb(p[0], p[1]); },
    setStampInstances,
  }),
}));
vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({ t: (key: string, p?: Record<string, unknown>) => (p ? `${key} ${JSON.stringify(p)}` : key) }),
}));

import { LineTool } from '../LineTool';

describe('LineTool', () => {
  it('start, end and spacing give one point per position along the row', () => {
    const onChange = vi.fn();
    render(<LineTool onInstancesChange={onChange} />);
    fireEvent.click(screen.getByText('wizard.line.pick_start'));
    fireEvent.click(screen.getByText('wizard.line.pick_end'));
    fireEvent.change(screen.getByLabelText('wizard.line.spacing'), { target: { value: '2' } });
    const last = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(last.map((p: { col: number }) => p.col)).toEqual([1, 2, 3, 4]);
    expect(screen.getByTestId('line-summary').textContent).toContain('"count":4');
    expect(setStampInstances.mock.calls[setStampInstances.mock.calls.length - 1][0]).toHaveLength(4);
  });
});
