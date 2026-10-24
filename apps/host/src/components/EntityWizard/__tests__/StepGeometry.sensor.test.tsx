import { describe, expect, it, vi, beforeEach } from 'vitest';
import type { Geometry } from 'geojson';
import type { ComponentProps } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

const wizard = vi.hoisted(() => ({
  entityType: 'Device' as string,
  formData: { macroCategory: 'sensors', geometry: null, parcels: [] } as Record<string, unknown>,
  updateFormData: vi.fn(),
  setValidationError: vi.fn(),
}));
const editor = vi.hoisted(() => ({ props: null as { parentGeometry?: unknown; outlineGeometry?: unknown } | null }));

vi.mock('../WizardContext', () => ({ useWizard: () => wizard }));
vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (k: string) => k }) }));
vi.mock('../GeometryEditor', () => ({
  GeometryEditor: (p: { parentGeometry?: unknown; outlineGeometry?: unknown }) => { editor.props = p; return <div data-testid="editor" />; },
}));
vi.mock('../StampTool', () => ({ StampTool: () => null }));
vi.mock('../ArrayTool', () => ({ ArrayTool: () => null }));
vi.mock('../AssetBrowser', () => ({ AssetBrowser: () => null }));
vi.mock('../PlacementModeSelector', () => ({ PlacementModeSelector: () => null }));

import { StepGeometry } from '../steps/StepGeometry';
import { validateGeometryStep } from '../iotValidation';

type Props = ComponentProps<typeof StepGeometry>;
const placement = { mode: 'single', stampedInstances: [] } as unknown as Props['placementState'];

function setup() {
  return render(<StepGeometry placementState={placement} dispatchPlacement={vi.fn()} />);
}
const lat = () => screen.getByLabelText('wizard.geometry.latitude') as HTMLInputElement;
const lon = () => screen.getByLabelText('wizard.geometry.longitude') as HTMLInputElement;
const lastGeometry = () => wizard.updateFormData.mock.calls[wizard.updateFormData.mock.calls.length - 1]?.[0]?.geometry;

describe('StepGeometry for Device', () => {
  beforeEach(() => {
    wizard.entityType = 'Device';
    wizard.formData = { macroCategory: 'sensors', geometry: null, parcels: [] };
    wizard.updateFormData.mockClear();
    wizard.setValidationError.mockClear();
  });

  it('typed lat/lon set a Point in [lon, lat] order', () => {
    setup();
    fireEvent.change(lat(), { target: { value: '42.8' } });
    fireEvent.change(lon(), { target: { value: '-1.6' } });
    expect(lastGeometry()).toEqual({ type: 'Point', coordinates: [-1.6, 42.8] });
  });

  it('accepts a decimal comma', () => {
    setup();
    fireEvent.change(lat(), { target: { value: '42,8' } });
    fireEvent.change(lon(), { target: { value: '-1,6' } });
    expect(lastGeometry()).toEqual({ type: 'Point', coordinates: [-1.6, 42.8] });
  });

  it.each([
    ['91', '-1.6'],
    ['42.8', '-181'],
    ['abc', '-1.6'],
  ])('lat %s lon %s sets no geometry and shows the message', (a, b) => {
    setup();
    fireEvent.change(lat(), { target: { value: a } });
    fireEvent.change(lon(), { target: { value: b } });
    expect(lastGeometry()).toBeNull();
    expect(screen.getByRole('alert').textContent).toBe('wizard.geometry.coordinates_invalid');
  });

  it('a map click fills both fields', () => {
    const { rerender } = setup();
    wizard.formData = { ...wizard.formData, geometry: { type: 'Point', coordinates: [-1.6, 42.8] } };
    rerender(<StepGeometry placementState={placement} dispatchPlacement={vi.fn()} />);
    expect(lat().value).toBe('42.8');
    expect(lon().value).toBe('-1.6');
  });

  it('draws the first parcel as display-only outline, never as containment parent', () => {
    const poly = { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] };
    wizard.formData = {
      macroCategory: 'sensors', geometry: null,
      parcels: [{ id: 'urn:p1', type: 'AgriParcel', name: 'P1', geometry: poly }],
    };
    setup();
    expect(editor.props?.outlineGeometry).toEqual({ id: 'urn:p1', name: 'P1', geometry: poly });
    expect(editor.props?.parentGeometry).toBeUndefined();
  });

  it('a point outside the parcel raises no validation error and passes the step', () => {
    const poly = { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] };
    wizard.formData = {
      macroCategory: 'sensors', geometry: null,
      parcels: [{ id: 'urn:p1', type: 'AgriParcel', name: 'P1', geometry: poly }],
    };
    setup();
    fireEvent.change(lat(), { target: { value: '40' } });
    fireEvent.change(lon(), { target: { value: '-3' } });
    expect(lastGeometry()).toEqual({ type: 'Point', coordinates: [-3, 40] });
    expect(wizard.setValidationError).not.toHaveBeenCalled();
    expect(validateGeometryStep('Device', lastGeometry())).toBeNull();
  });

  it('does not show coordinate inputs for other types', () => {
    wizard.entityType = 'WeatherObserved';
    setup();
    expect(screen.queryByLabelText('wizard.geometry.latitude')).toBeNull();
    expect(editor.props?.parentGeometry).toBeUndefined();
  });
});

describe('validateGeometryStep', () => {
  const point: Geometry = { type: 'Point', coordinates: [0, 0] };
  it('requires a Point for Device only', () => {
    expect(validateGeometryStep('Device', null)).toBe('wizard.validation.point_required');
    expect(validateGeometryStep('Device', { type: 'Polygon', coordinates: [] } as Geometry)).toBe('wizard.validation.point_required');
    expect(validateGeometryStep('Device', point)).toBeNull();
    expect(validateGeometryStep('WeatherObserved', null)).toBeNull();
    expect(validateGeometryStep('Building', null)).toBeNull();
    expect(validateGeometryStep(null, null)).toBeNull();
  });
});
