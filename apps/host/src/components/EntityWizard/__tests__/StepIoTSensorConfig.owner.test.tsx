/* eslint-disable @typescript-eslint/no-explicit-any */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';

const K = 'urn:ngsi-ld:AgriSolarPark:k';
const J = 'urn:ngsi-ld:AgriSolarPark:j';
const A = 'urn:ngsi-ld:AgriParcel:a';
const B = 'urn:ngsi-ld:AgriParcel:b';
const rel = (object: string) => ({ type: 'Relationship', object });
const parcel = (id: string) => ({ id, type: 'AgriParcel', name: id, geometry: null });

const { getSDMEntityInstances } = vi.hoisted(() => ({ getSDMEntityInstances: vi.fn() }));
vi.mock('@/services/api', () => ({ default: { getSDMEntityInstances } }));
vi.mock('@/services/deviceProfilesApi', () => ({
  listDeviceProfiles: vi.fn().mockResolvedValue([]),
  createDeviceProfile: vi.fn(),
}));
vi.mock('@/services/dataloggersApi', () => ({ listSensorProfiles: vi.fn().mockResolvedValue([]) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/context/ToastContext', () => ({ useToastContext: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock('../../DeviceProfileHelpModal', () => ({ DeviceProfileHelpModal: () => null }));
vi.mock('../ParentEntitySelector', () => ({
  ParentEntitySelector: ({ onSelectMany }: any) => (
    <div>
      <button onClick={() => onSelectMany([parcel(A)])}>pick-a</button>
      <button onClick={() => onSelectMany([parcel(B)])}>pick-b</button>
    </div>
  ),
}));
vi.mock('@nekazari/ui-kit', async () => {
  const actual = await vi.importActual<typeof import('@nekazari/ui-kit')>('@nekazari/ui-kit');
  return {
    ...actual,
    Select: ({ value, onValueChange, options }: any) => (
      <select aria-label="owner" value={value} onChange={e => onValueChange(e.target.value)}>
        {options.map((o: any) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    ),
  };
});

let latest: any = null;
vi.mock('../WizardContext', () => ({ useWizard: () => latest.ctx }));

import { StepIoTSensorConfig } from '../steps/StepIoTSensorConfig';

function Harness({ type = 'Device' }: { type?: string }) {
  const [formData, setFormData] = useState<any>({
    macroCategory: 'sensors', name: 'S', parcels: [], ownerParkId: null, deviceProfileId: null,
  });
  latest = {
    ctx: {
      entityType: type,
      formData,
      updateFormData: (patch: any) => setFormData((p: any) => ({ ...p, ...patch })),
    },
  };
  const select = latest.ctx;
  return <div data-testid="owner-state">{String(select.formData.ownerParkId)}<StepIoTSensorConfig /></div>;
}

beforeEach(() => {
  getSDMEntityInstances.mockReset().mockResolvedValue([
    { id: K, type: 'AgriSolarPark', name: { value: 'East array' }, hasAgriParcel: rel(A) },
    { id: J, type: 'AgriSolarPark', name: 'West array', hasAgriParcel: rel(B) },
  ]);
});

describe('StepIoTSensorConfig owner', () => {
  it('lists only the parks of the selected parcels and resets the owner when the parcel goes', async () => {
    render(<Harness />);
    expect(screen.queryByLabelText('owner')).toBeNull();

    fireEvent.click(screen.getByText('pick-a'));
    const select = (await screen.findByLabelText('owner')) as HTMLSelectElement;
    expect(Array.from(select.options).map(o => o.value)).toEqual(['parcel', K]);

    fireEvent.change(select, { target: { value: K } });
    await waitFor(() => expect(screen.getByTestId('owner-state').textContent).toContain(K));

    fireEvent.click(screen.getByText('pick-b'));
    await waitFor(() => expect(screen.getByTestId('owner-state').textContent).toMatch(/^null/));
    const after = (await screen.findByLabelText('owner')) as HTMLSelectElement;
    expect(Array.from(after.options).map(o => o.value)).toEqual(['parcel', J]);
  });

  it('does not ask for parks for other sensor types', () => {
    render(<Harness type="WeatherObserved" />);
    expect(getSDMEntityInstances).not.toHaveBeenCalledWith('AgriSolarPark');
  });
});
