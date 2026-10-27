import { beforeEach, describe, expect, it, vi } from 'vitest';

const { createSDMEntity, createParcel } = vi.hoisted(() => ({ createSDMEntity: vi.fn(), createParcel: vi.fn() }));
vi.mock('@/services/api', () => ({ default: { createSDMEntity } }));
vi.mock('@/services/parcelApi', () => ({ parcelApi: { createParcel } }));

import { submitGeoAsset } from '../submission/submitGeoAsset';
import type { GeoAssetFormData } from '../types';
import type { PlacementState } from '@/machines/placementMachine';

function form(additionalAttributes: Record<string, unknown> = {}): GeoAssetFormData {
  return {
    macroCategory: 'energy',
    name: 'T1',
    description: '',
    geometry: { type: 'Point', coordinates: [1, 2] },
    geometryType: 'Point',
    additionalAttributes,
    modelScale: 1,
    modelRotation: [0, 0, 0],
  } as unknown as GeoAssetFormData;
}

const arrayState = {
  mode: 'array',
  stampedInstances: [{ lng: 1, lat: 2 }],
  arraySettings: { bearing: 90, tilt: 30, nominalPower: 500 },
} as unknown as PlacementState;

describe('submitGeoAsset AgriEnergyTracker array', () => {
  beforeEach(() => {
    createSDMEntity.mockReset();
    createSDMEntity.mockResolvedValue({});
  });

  it('does not invent a panel size', async () => {
    await submitGeoAsset('AgriEnergyTracker', form(), arrayState);
    const body = createSDMEntity.mock.calls[0][1];
    expect(body.panelDimension).toBeUndefined();
    expect(body.tilt).toEqual({ type: 'Property', value: 30 });
    expect(body.NominalPower).toEqual({ type: 'Property', value: 500 });
  });

  it('keeps a panel size given by the user', async () => {
    const dim = { width: 1.1, length: 1.7, thickness: 0.03 };
    await submitGeoAsset('AgriEnergyTracker', form({ panelDimension: dim }), arrayState);
    expect(createSDMEntity.mock.calls[0][1].panelDimension).toEqual({ type: 'Property', value: dim });
  });
});

describe('submitGeoAsset AgriParcel subdivision', () => {
  const parent = { id: 'urn:ngsi-ld:AgriParcel:p-1', type: 'AgriParcel', name: 'P', geometry: null };
  const parcelForm = (extra: Record<string, unknown>) =>
    ({ ...form(), geometry: { type: 'Polygon', coordinates: [] }, ...extra }) as unknown as GeoAssetFormData;

  beforeEach(() => {
    createParcel.mockReset();
    createParcel.mockResolvedValue({});
  });

  it('passes the parent of a subdivision', async () => {
    await submitGeoAsset('AgriParcel', parcelForm({ isSubdivision: true, parentEntity: parent }), arrayState);
    expect(createParcel.mock.calls[0][0].refParent).toBe(parent.id);
  });

  it('passes no parent when the parcel is not a subdivision', async () => {
    await submitGeoAsset('AgriParcel', parcelForm({ isSubdivision: false, parentEntity: parent }), arrayState);
    expect(createParcel.mock.calls[0][0].refParent).toBeUndefined();
  });
});
