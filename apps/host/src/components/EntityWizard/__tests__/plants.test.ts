import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PlacementState } from '@/machines/placementMachine';
import type { GeoAssetFormData } from '../types';

const { previewPlants, createPlants } = vi.hoisted(() => ({
  previewPlants: vi.fn(),
  createPlants: vi.fn(),
}));
vi.mock('@/services/api', () => ({ default: { previewPlants, createPlants } }));

import { plantsRequest, toPlantItems } from '../submission/plants';

const PARCEL = { id: 'urn:ngsi-ld:AgriParcel:demo-parcel-001', type: 'AgriParcel', name: 'P', geometry: null };

function form(extra: Partial<GeoAssetFormData> = {}, attrs: Record<string, string | number> = {}): GeoAssetFormData {
  return {
    macroCategory: 'assets', name: '', description: '', geometryType: 'Point', parentEntity: PARCEL,
    isSubdivision: false, additionalAttributes: attrs, geometry: null, ...extra,
  } as unknown as GeoAssetFormData;
}

function placement(mode: string, instances: Array<Record<string, number>> = []): PlacementState {
  return { mode, stampedInstances: instances } as unknown as PlacementState;
}

describe('toPlantItems', () => {
  it('a single point is one plant with the given code and batch attributes', () => {
    const items = toPlantItems(
      placement('single'),
      form({ geometry: { type: 'Point', coordinates: [-1.6, 42.8] } as any },
        { plantCode: 'T-1', variety: 'Arbequina', datePlanted: '2001-03-15', plantStatus: 'alive' }),
    );
    expect(items).toEqual([{ lat: 42.8, lon: -1.6, plantCode: 'T-1', variety: 'Arbequina',
      datePlanted: '2001-03-15', plantStatus: 'alive' }]);
  });

  it('a grid keeps row and position of every point', () => {
    const inst = [1, 2].flatMap((row) => [1, 2, 3].map((col) => ({ lat: 42.8 + row, lng: -1.6 + col, row, col })));
    const items = toPlantItems(placement('array', inst), form());
    expect(items).toHaveLength(6);
    expect(items.map((i) => [i.rowNumber, i.positionInRow])).toEqual([[1, 1], [1, 2], [1, 3], [2, 1], [2, 2], [2, 3]]);
    expect(items.every((i) => i.plantCode === undefined)).toBe(true);
  });

  it('a line uses the row number of the form and the position along the line', () => {
    const inst = [1, 2, 3].map((col) => ({ lat: 42.8, lng: -1.6 + col * 1e-5, col }));
    const items = toPlantItems(placement('line', inst), form({}, { rowNumber: 7 }));
    expect(items.map((i) => [i.rowNumber, i.positionInRow])).toEqual([[7, 1], [7, 2], [7, 3]]);
  });

  it('stamped points without indices are plain plants', () => {
    const items = toPlantItems(placement('stamp', [{ lat: 42.8, lng: -1.6 }]), form());
    expect(items).toEqual([{ lat: 42.8, lon: -1.6 }]);
  });
});

describe('plantsRequest', () => {
  beforeEach(() => vi.clearAllMocks());
  it('sends the parcel and the items, nothing else', () => {
    const body = plantsRequest(placement('stamp', [{ lat: 42.8, lng: -1.6 }]), form());
    expect(body).toEqual({ parcel_id: PARCEL.id, plants: [{ lat: 42.8, lon: -1.6 }] });
  });
  it('without a parcel there is no request', () => {
    expect(() => plantsRequest(placement('single'), form({ parentEntity: null }))).toThrow();
  });
});

describe('code prefix', () => {
  it('is sent when set in the form', () => {
    const body = plantsRequest(placement('stamp', [{ lat: 42.8, lng: -1.6 }]), form({}, { codePrefix: 'B' }));
    expect(body.code_prefix).toBe('B');
  });
});

