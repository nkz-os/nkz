/* eslint-disable @typescript-eslint/no-explicit-any */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const PARCEL = 'urn:ngsi-ld:AgriParcel:a';
const OTHER = 'urn:ngsi-ld:AgriParcel:zz';
const rel = (object: string) => ({ type: 'Relationship', object });

const { getSDMEntityInstances } = vi.hoisted(() => ({ getSDMEntityInstances: vi.fn() }));
vi.mock('@/services/api', () => ({ default: { getSDMEntityInstances } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
// Radix Select needs a real browser; a native select keeps the same props contract.
vi.mock('@nekazari/ui-kit', () => ({
  Select: ({ value, onValueChange, options, 'aria-label': aria }: any) => (
    <select aria-label={aria} value={value} onChange={(e) => onValueChange(e.target.value)}>
      {options.map((o: any) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  ),
  Input: (props: any) => <input {...props} />,
  Button: ({ variant: _v, children, ...rest }: any) => <button {...rest}>{children}</button>,
}));

import { ObservationFields, type ObservationValue } from '../steps/ObservationFields';

function mount(value: ObservationValue, over: Record<string, unknown> = {}) {
  const onChange = vi.fn();
  render(
    <ObservationFields
      value={value}
      onChange={onChange}
      profileAttributes={['temperature', 'soilMoisture']}
      parcelIds={[PARCEL]}
      {...over}
    />,
  );
  return onChange;
}

describe('ObservationFields', () => {
  beforeEach(() => {
    getSDMEntityInstances.mockReset();
    getSDMEntityInstances.mockImplementation(async (type: string) => {
      if (type === 'AgriParcel') return [{ id: PARCEL, name: 'Parcel A' }, { id: OTHER, name: 'Other' }];
      if (type === 'AgriPlant') return [{ id: 'urn:ngsi-ld:AgriPlant:p1', name: 'Plant 1', hasAgriParcel: rel(PARCEL) }];
      return [];
    });
  });

  it('renders_depth_inputs_only_for_soil', () => {
    const { unmount } = render(
      <ObservationFields value={{ observationContext: { medium: 'soil' } }} onChange={vi.fn()} profileAttributes={[]} parcelIds={[PARCEL]} />,
    );
    expect(screen.getByTestId('obs-depthFrom')).toBeTruthy();
    expect(screen.getByTestId('obs-depthTo')).toBeTruthy();
    unmount();
    render(
      <ObservationFields value={{ observationContext: { medium: 'ambientAir' } }} onChange={vi.fn()} profileAttributes={[]} parcelIds={[PARCEL]} />,
    );
    expect(screen.queryByTestId('obs-depthFrom')).toBeNull();
    expect(screen.getByTestId('obs-heightAboveGround')).toBeTruthy();
  });

  it('requires_structure_for_under_structure', () => {
    mount({ observationContext: { medium: 'ambientAirUnderStructure' } });
    expect(screen.getByText('wizard.observation.error.structure_required')).toBeTruthy();
  });

  it('override_rows_follow_profile_attributes', () => {
    mount({ observationContext: { medium: 'ambientAir' } });
    fireEvent.click(screen.getByTestId('obs-overrides-toggle'));
    expect(screen.getByTestId('obs-override-temperature')).toBeTruthy();
    expect(screen.getByTestId('obs-override-soilMoisture')).toBeTruthy();
    expect(screen.queryByTestId('obs-override-humidity')).toBeNull();
  });

  it('limits_features_to_selected_parcels', async () => {
    mount({ observationContext: { medium: 'plant' } });
    await waitFor(() => expect(screen.getByRole('option', { name: /Plant 1/ })).toBeTruthy());
    expect(screen.getByRole('option', { name: /Parcel A/ })).toBeTruthy();
    expect(screen.queryByRole('option', { name: /Other/ })).toBeNull();
  });

  it('emits_cleaned_context_on_medium_change', () => {
    const onChange = mount({ observationContext: { medium: 'soil', depthFrom: 0, depthTo: 10 } });
    fireEvent.change(screen.getByLabelText('wizard.observation.medium'), { target: { value: 'plant' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ observationContext: { medium: 'plant' } }));
  });
});
