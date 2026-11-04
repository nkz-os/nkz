import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { previewPlants, createPlants } = vi.hoisted(() => ({ previewPlants: vi.fn(), createPlants: vi.fn() }));
vi.mock('@/services/api', () => ({ default: { previewPlants, createPlants } }));
vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({ t: (key: string, p?: Record<string, unknown>) => (p ? `${key} ${JSON.stringify(p)}` : key) }),
}));

import { PlantFileImport, fileFormat } from '../PlantFileImport';

const PARCEL = 'urn:ngsi-ld:AgriParcel:demo-parcel-001';
const PREVIEW = {
  accepted: [{ index: 0, id: 'x', plantCode: 'T-1', action: 'create' }],
  rejected: [{ index: 1, plantCode: 'T-2', reason: 'outside_parcel', distance_m: 40 }],
  warnings: [],
};

function upload(name: string, text: string) {
  fireEvent.change(screen.getByLabelText('wizard.plants.file_label'), { target: { files: [new File([text], name)] } });
}

describe('PlantFileImport', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    previewPlants.mockResolvedValue(PREVIEW);
    createPlants.mockResolvedValue({ written: [{ id: 'x', plantCode: 'T-1' }], rejected: PREVIEW.rejected, warnings: [] });
  });

  it('maps extensions to formats', () => {
    expect(['a.gpx', 'a.KML', 'a.kmz', 'a.geojson', 'a.json', 'a.csv', 'a.shp'].map(fileFormat))
      .toEqual(['gpx', 'kml', 'kmz', 'geojson', 'geojson', 'csv', null]);
  });

  it('previews a GPX (base64) and shows what is left out, then imports on confirm', async () => {
    render(<PlantFileImport parcelId={PARCEL} />);
    upload('trees.gpx', '<gpx/>');
    await waitFor(() => expect(previewPlants).toHaveBeenCalledWith({
      parcel_id: PARCEL, file: { format: 'gpx', content: btoa('<gpx/>') } }));
    expect((await screen.findByTestId('plant-file-rejected')).textContent).toContain('wizard.plants.reasons.outside_parcel');
    expect(createPlants).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('wizard.plants.import_confirm'));
    await waitFor(() => expect(createPlants).toHaveBeenCalledWith({
      parcel_id: PARCEL, file: { format: 'gpx', content: btoa('<gpx/>') } }));
    expect((await screen.findByRole('status')).textContent).toContain('"count":1');
  });

  it('sends CSV as text', async () => {
    render(<PlantFileImport parcelId={PARCEL} />);
    upload('trees.csv', 'plantCode,lat,lon\n');
    await waitFor(() => expect(previewPlants).toHaveBeenCalledWith({
      parcel_id: PARCEL, file: { format: 'csv', content: 'plantCode,lat,lon\n' } }));
  });

  it('an unsupported file is refused without calling the server', () => {
    render(<PlantFileImport parcelId={PARCEL} />);
    upload('trees.shp', 'x');
    expect(previewPlants).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toBe('wizard.plants.file_unsupported');
  });
});
