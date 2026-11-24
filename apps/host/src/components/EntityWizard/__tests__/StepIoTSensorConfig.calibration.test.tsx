/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';

vi.mock('@/services/api', () => ({ default: { getSDMEntityInstances: vi.fn().mockResolvedValue([]) } }));
vi.mock('@/services/deviceProfilesApi', () => ({
  listDeviceProfiles: vi.fn().mockResolvedValue([]),
  createDeviceProfile: vi.fn(),
}));
vi.mock('@/services/dataloggersApi', () => ({ listSensorProfiles: vi.fn().mockResolvedValue([]) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/context/ToastContext', () => ({ useToastContext: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock('../steps/ObservationFields', () => ({ ObservationFields: () => null }));
vi.mock('../../DeviceProfileHelpModal', () => ({ DeviceProfileHelpModal: () => null }));
vi.mock('../ParentEntitySelector', () => ({ ParentEntitySelector: () => null }));

let latest: any = null;
vi.mock('../WizardContext', () => ({ useWizard: () => latest }));

import { StepIoTSensorConfig } from '../steps/StepIoTSensorConfig';

function Harness({ initial }: { initial?: Record<string, unknown> }) {
  const [formData, setFormData] = useState<any>({
    macroCategory: 'sensors', name: 'S', parcels: [], ownerParkId: null, deviceProfileId: null, ...initial,
  });
  latest = {
    entityType: 'Device',
    formData,
    updateFormData: (patch: any) => setFormData((p: any) => ({ ...p, ...patch })),
  };
  return (
    <>
      <StepIoTSensorConfig />
      <pre data-testid="state">{JSON.stringify(formData)}</pre>
    </>
  );
}

function addVariable(name: string) {
  fireEvent.change(screen.getByPlaceholderText('wizard.iot.new_variable'), { target: { value: name } });
  fireEvent.click(screen.getByText('wizard.iot.add'));
}

describe('StepIoTSensorConfig calibration fields', () => {
  it('shows validity inputs and no calibration inputs, and does not write calibrationConfig', () => {
    render(<Harness />);
    addVariable('soil_moisture');

    expect(screen.getByText('wizard.iot.min_valid')).toBeTruthy();
    expect(screen.getByText('wizard.iot.max_valid')).toBeTruthy();
    expect(screen.getByText('wizard.iot.max_stagnant')).toBeTruthy();
    expect(screen.queryByText('wizard.iot.slope')).toBeNull();
    expect(screen.queryByText('wizard.iot.offset')).toBeNull();
    expect(screen.queryByText('wizard.iot.hardware_id')).toBeNull();
    expect(screen.queryByPlaceholderText('SN-...')).toBeNull();

    const state = JSON.parse(screen.getByTestId('state').textContent ?? '{}');
    expect(state.healthConfig).toEqual({ soil_moisture: {} });
    expect('calibrationConfig' in state).toBe(false);
  });

  it('leaves an existing calibrationConfig untouched', () => {
    const existing = { x: { slope: 2, offset: 1, sensorHardwareId: 'a' } };
    render(<Harness initial={{ calibrationConfig: existing }} />);
    addVariable('y');
    fireEvent.click(screen.getAllByTitle('wizard.iot.remove_variable')[0]);
    const state = JSON.parse(screen.getByTestId('state').textContent ?? '{}');
    expect(state.calibrationConfig).toEqual(existing);
  });
});
