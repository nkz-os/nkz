/* eslint-disable @typescript-eslint/no-explicit-any */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';

const { listDataloggers } = vi.hoisted(() => ({ listDataloggers: vi.fn() }));
vi.mock('@/services/dataloggersApi', async () => {
  const actual = await vi.importActual<typeof import('@/services/dataloggersApi')>('@/services/dataloggersApi');
  return { ...actual, listDataloggers };
});
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
const auth = vi.hoisted(() => ({ roles: ['TechnicalConsultant'] as string[] }));
vi.mock('@/context/KeycloakAuthContext', () => ({
  useAuth: () => ({ hasAnyRole: (r: string[]) => r.some(x => auth.roles.includes(x)) }),
}));
vi.mock('@nekazari/ui-kit', async () => {
  const actual = await vi.importActual<typeof import('@nekazari/ui-kit')>('@nekazari/ui-kit');
  return {
    ...actual,
    Select: ({ value, onValueChange, options, placeholder }: any) => (
      <select aria-label={placeholder ?? 'select'} value={value} onChange={e => onValueChange(e.target.value)}>
        <option value="">-</option>
        {options.map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    ),
  };
});

let latest: any = null;
vi.mock('../WizardContext', () => ({ useWizard: () => latest.ctx }));

import { StepDatalogger } from '../steps/StepDatalogger';

function Harness() {
  const [formData, setFormData] = useState<any>({
    macroCategory: 'sensors', name: 'Probe Nº 1', dataloggerMode: 'existing', dataloggerId: '', externalId: '', newDataloggerKind: 'datak',
  });
  latest = { ctx: { formData, updateFormData: (p: any) => setFormData((prev: any) => ({ ...prev, ...p })) } };
  return <div data-testid="state">{JSON.stringify(formData)}<StepDatalogger /></div>;
}
const state = () => JSON.parse(screen.getByTestId('state').textContent!.split('}')[0] + '}');

beforeEach(() => {
  auth.roles = ['TechnicalConsultant'];
  listDataloggers.mockReset().mockResolvedValue([
    { id: 'dl-1', name: 'Field A', kind: 'datak', username: 'dl_a', status: 'active', devices: [] },
    { id: 'dl-2', name: 'Revoked', kind: 'generic_mqtt', username: 'dl_b', status: 'revoked', devices: [] },
  ]);
});

describe('StepDatalogger', () => {
  it('tells a Farmer that an administrator or technical consultant registers the sensor', () => {
    auth.roles = ['Farmer'];
    render(<Harness />);
    expect(screen.getByText('wizard.datalogger.role_required')).toBeTruthy();
    expect(screen.queryByLabelText('wizard.datalogger.select_placeholder')).toBeNull();
  });

  it('lists only active dataloggers and stores the chosen id', async () => {
    render(<Harness />);
    const select = (await screen.findByLabelText('wizard.datalogger.select_placeholder')) as HTMLSelectElement;
    await waitFor(() => expect(Array.from(select.options).map(o => o.value)).toEqual(['', 'dl-1']));
    fireEvent.change(select, { target: { value: 'dl-1' } });
    await waitFor(() => expect(state().dataloggerId).toBe('dl-1'));
  });

  it('suggests the external id from the name and keeps a typed one', async () => {
    render(<Harness />);
    await waitFor(() => expect(state().externalId).toBe('Probe_N_1'));
    const input = screen.getByPlaceholderText('wizard.datalogger.external_id_placeholder');
    fireEvent.change(input, { target: { value: 'my.probe-2' } });
    await waitFor(() => expect(state().externalId).toBe('my.probe-2'));
  });

  it('falls back to a new datalogger when there is none', async () => {
    listDataloggers.mockResolvedValue([]);
    render(<Harness />);
    await waitFor(() => expect(state().dataloggerMode).toBe('new'));
    expect(screen.getByPlaceholderText('wizard.datalogger.name_placeholder')).toBeInTheDocument();
  });
});
