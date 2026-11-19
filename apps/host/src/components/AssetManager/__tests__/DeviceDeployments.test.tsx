/* eslint-disable @typescript-eslint/no-explicit-any */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const DEVICE = 'urn:ngsi-ld:Device:t1:d1';
const { get, post, put, getSDMEntityInstances } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  getSDMEntityInstances: vi.fn(),
}));

vi.mock('@/services/api', () => ({ default: { get, post, put, getSDMEntityInstances } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (k: string) => k }) }));
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

import { DeviceDeployments, readDeviceProfileCode } from '../DeviceDeployments';

const dep = (id: string, from: string, to: string | null, medium: string, extra: object = {}) => ({
  id,
  device_urn: DEVICE,
  valid_from: from,
  valid_to: to,
  observesFeature: null,
  observationContext: { medium, ...extra },
  channelOverrides: {},
  mobility: 'fixed',
  location: { lat: 42, lon: -1 },
});

const NEWEST = dep('dep-2', '2026-06-01T00:00:00+00:00', null, 'soil', { depthFrom: 0, depthTo: 10 });
const OLDEST = dep('dep-1', '2026-01-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00', 'ambientAir');

beforeEach(() => {
  get.mockReset().mockImplementation(async (url: string) =>
    url === '/api/sensors/profiles'
      ? { data: { profiles: [{ code: 'prof-1', name: 'P', sdm_attributes: ['temperature'] }] } }
      : { data: { deployments: [NEWEST, OLDEST] } },
  );
  post.mockReset().mockResolvedValue({ data: NEWEST });
  put.mockReset().mockResolvedValue({ data: NEWEST });
  getSDMEntityInstances.mockReset().mockResolvedValue([]);
});

describe('DeviceDeployments', () => {
  it('lists_deployments_newest_first', async () => {
    render(<DeviceDeployments deviceUrn={DEVICE} />);
    const rows = await screen.findAllByTestId('deployment-row');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('wizard.observation.medium_soil');
    expect(rows[1].textContent).toContain('wizard.observation.medium_ambientAir');
    expect(get).toHaveBeenCalledWith(`/api/sensors/devices/${encodeURIComponent(DEVICE)}/deployments`);
  });

  it('correct_requires_reason', async () => {
    render(<DeviceDeployments deviceUrn={DEVICE} />);
    fireEvent.click((await screen.findAllByTestId('deployment-correct'))[1]);
    fireEvent.change(screen.getByTestId('correct-validTo'), { target: { value: '2026-05-30T10:00' } });
    fireEvent.click(screen.getByTestId('correct-submit'));
    expect(await screen.findByText('entities.assets.deployments.reason_required')).toBeTruthy();
    expect(put).not.toHaveBeenCalled();
  });

  it('shows_overlap_error_from_server', async () => {
    put.mockRejectedValue({ response: { status: 409, data: { error: 'Overlaps another deployment of this device' } } });
    render(<DeviceDeployments deviceUrn={DEVICE} />);
    fireEvent.click((await screen.findAllByTestId('deployment-correct'))[1]);
    fireEvent.change(screen.getByTestId('correct-validTo'), { target: { value: '2026-05-30T10:00' } });
    fireEvent.change(screen.getByTestId('correct-reason'), { target: { value: 'wrong end date' } });
    fireEvent.click(screen.getByTestId('correct-submit'));
    expect(await screen.findByText('Overlaps another deployment of this device')).toBeTruthy();
    expect(put).toHaveBeenCalledTimes(1);
    const [url, body] = put.mock.calls[0];
    expect(url).toBe('/api/sensors/deployments/dep-1');
    expect(body.reason).toBe('wrong end date');
    expect(body.validTo).toMatch(/^2026-05-30T10:00:00[+-]\d\d:\d\d$/);
    expect(body).not.toHaveProperty('mobility');
  });

  it('move posts reason and validFrom and reloads', async () => {
    render(<DeviceDeployments deviceUrn={DEVICE} />);
    fireEvent.click(await screen.findByTestId('deployment-move'));
    fireEvent.change(screen.getByTestId('move-validFrom'), { target: { value: '2026-07-01T09:00' } });
    fireEvent.change(screen.getByTestId('move-reason'), { target: { value: 'moved to the orchard' } });
    fireEvent.click(screen.getByTestId('move-submit'));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const [url, body] = post.mock.calls[0];
    expect(url).toBe(`/api/sensors/devices/${encodeURIComponent(DEVICE)}/deployments`);
    expect(body.reason).toBe('moved to the orchard');
    expect(body.validFrom).toMatch(/^2026-07-01T09:00:00[+-]\d\d:\d\d$/);
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  });

  it('move prefills location from the current deployment and sends it', async () => {
    render(<DeviceDeployments deviceUrn={DEVICE} />);
    fireEvent.click(await screen.findByTestId('deployment-move'));
    expect((screen.getByTestId('move-lat') as HTMLInputElement).value).toBe('42');
    expect((screen.getByTestId('move-lon') as HTMLInputElement).value).toBe('-1');
    fireEvent.change(screen.getByTestId('move-lat'), { target: { value: '43.5' } });
    fireEvent.change(screen.getByTestId('move-lon'), { target: { value: '-2.25' } });
    fireEvent.change(screen.getByTestId('move-reason'), { target: { value: 'moved' } });
    fireEvent.click(screen.getByTestId('move-submit'));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1].location).toEqual({ lat: 43.5, lon: -2.25 });
  });

  it('move requires a location for a fixed device and rejects out-of-range values', async () => {
    render(<DeviceDeployments deviceUrn={DEVICE} />);
    fireEvent.click(await screen.findByTestId('deployment-move'));
    fireEvent.change(screen.getByTestId('move-reason'), { target: { value: 'r' } });
    fireEvent.change(screen.getByTestId('move-lat'), { target: { value: '' } });
    fireEvent.change(screen.getByTestId('move-lon'), { target: { value: '' } });
    fireEvent.click(screen.getByTestId('move-submit'));
    expect(await screen.findByText('entities.assets.deployments.location_required')).toBeTruthy();
    fireEvent.change(screen.getByTestId('move-lat'), { target: { value: '91' } });
    fireEvent.change(screen.getByTestId('move-lon'), { target: { value: '0' } });
    fireEvent.click(screen.getByTestId('move-submit'));
    expect(await screen.findByText('entities.assets.deployments.location_invalid')).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });

  it('move sends observesFeature null when the user cleared it', async () => {
    const FEATURE = 'urn:ngsi-ld:AgriParcel:a';
    get.mockImplementation(async () => ({ data: { deployments: [{ ...NEWEST, observesFeature: FEATURE }, OLDEST] } }));
    render(<DeviceDeployments deviceUrn={DEVICE} parcelIds={[FEATURE]} />);
    fireEvent.click(await screen.findByTestId('deployment-move'));
    fireEvent.change(screen.getByLabelText('wizard.observation.feature'), { target: { value: '__none__' } });
    fireEvent.change(screen.getByTestId('move-reason'), { target: { value: 'no longer tied to a parcel' } });
    fireEvent.click(screen.getByTestId('move-submit'));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toHaveProperty('observesFeature', null);
  });

  it('move leaves observesFeature out when it was never set', async () => {
    render(<DeviceDeployments deviceUrn={DEVICE} />);
    fireEvent.click(await screen.findByTestId('deployment-move'));
    fireEvent.change(screen.getByTestId('move-reason'), { target: { value: 'r' } });
    fireEvent.click(screen.getByTestId('move-submit'));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).not.toHaveProperty('observesFeature');
  });

  it('correct sends observesFeature null when the user cleared it', async () => {
    const FEATURE = 'urn:ngsi-ld:AgriParcel:a';
    get.mockImplementation(async () => ({ data: { deployments: [{ ...NEWEST, observesFeature: FEATURE }] } }));
    render(<DeviceDeployments deviceUrn={DEVICE} parcelIds={[FEATURE]} />);
    fireEvent.click(await screen.findByTestId('deployment-correct'));
    fireEvent.change(screen.getByLabelText('wizard.observation.feature'), { target: { value: '__none__' } });
    fireEvent.change(screen.getByTestId('correct-reason'), { target: { value: 'wrong feature' } });
    fireEvent.click(screen.getByTestId('correct-submit'));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    expect(put.mock.calls[0][1]).toHaveProperty('observesFeature', null);
  });

  it('offers the channels of the device profile for overrides', async () => {
    render(<DeviceDeployments deviceUrn={DEVICE} profileCode="prof-1" />);
    fireEvent.click(await screen.findByTestId('deployment-move'));
    expect(await screen.findByText('wizard.observation.overrides_title')).toBeTruthy();
  });

  it('falls back to the override keys when the profile cannot be resolved', async () => {
    const withOverrides = { ...NEWEST, channelOverrides: { soilMoisture: { observesFeature: 'urn:ngsi-ld:AgriParcel:a' } } };
    get.mockImplementation(async (url: string) => {
      if (url === '/api/sensors/profiles') throw new Error('down');
      return { data: { deployments: [withOverrides] } };
    });
    render(<DeviceDeployments deviceUrn={DEVICE} profileCode="prof-1" />);
    fireEvent.click(await screen.findByTestId('deployment-move'));
    expect(await screen.findByText('wizard.observation.overrides_title')).toBeTruthy();
  });

  it('readDeviceProfileCode reads hasDeviceProfile or refDeviceProfile, object or string', () => {
    const urn = 'urn:ngsi-ld:DeviceProfile:prof-1';
    expect(readDeviceProfileCode({ hasDeviceProfile: { type: 'Relationship', object: urn } })).toBe('prof-1');
    expect(readDeviceProfileCode({ refDeviceProfile: urn })).toBe('prof-1');
    expect(readDeviceProfileCode({})).toBeNull();
  });

  it('readDeviceProfileCode reads profileCode first (short or full-IRI key)', () => {
    const urn = 'urn:ngsi-ld:DeviceProfile:other';
    expect(readDeviceProfileCode({ profileCode: { type: 'Property', value: 'prof-1' } })).toBe('prof-1');
    expect(readDeviceProfileCode({ profileCode: 'prof-1' })).toBe('prof-1');
    expect(
      readDeviceProfileCode({ 'https://nkz-os.org/ns/profileCode': { type: 'Property', value: 'prof-1' }, hasDeviceProfile: urn }),
    ).toBe('prof-1');
  });
});
