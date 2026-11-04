import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { PlacementState } from '@/machines/placementMachine';

const { previewPlants } = vi.hoisted(() => ({ previewPlants: vi.fn() }));
vi.mock('@/services/api', () => ({ default: { previewPlants } }));
vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({ t: (key: string, p?: Record<string, unknown>) => (p ? `${key} ${JSON.stringify(p)}` : key) }),
}));

import { PlantPreview } from '../steps/PlantPreview';

const FORM = {
  macroCategory: 'assets', parentEntity: { id: 'urn:ngsi-ld:AgriParcel:demo-parcel-001', type: 'AgriParcel', name: 'P', geometry: null },
  additionalAttributes: {}, geometry: { type: 'Point', coordinates: [-1.6, 42.8] },
} as any;
const PLACEMENT = { mode: 'single', stampedInstances: [] } as unknown as PlacementState;

describe('PlantPreview', () => {
  it('shows what will be created and why something is left out', async () => {
    previewPlants.mockResolvedValue({
      accepted: [{ index: 0, id: 'x', plantCode: '1', action: 'create' }],
      rejected: [{ index: 1, plantCode: 'far', reason: 'outside_parcel', distance_m: 40 }],
      warnings: [{ index: 0, plantCode: '1', code: 'near_boundary', distance_m: 6 }],
    });
    render(<PlantPreview placementState={PLACEMENT} formData={FORM} />);
    await waitFor(() => expect(screen.getByTestId('plant-preview')).toBeTruthy());
    expect(previewPlants).toHaveBeenCalledWith({ parcel_id: FORM.parentEntity.id,
      plants: [{ lat: 42.8, lon: -1.6 }] });
    expect(screen.getByTestId('plant-preview').textContent).toContain('"created":1');
    expect(screen.getByTestId('plant-rejected').textContent).toContain('wizard.plants.reasons.outside_parcel');
    expect(screen.getByTestId('plant-warnings').textContent).toContain('wizard.plants.warnings.near_boundary');
  });

  it('says so when the check fails', async () => {
    previewPlants.mockRejectedValue(new Error('down'));
    render(<PlantPreview placementState={PLACEMENT} formData={FORM} />);
    expect((await screen.findByRole('alert')).textContent).toBe('wizard.plants.preview_failed');
  });
});
