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

import { DeviceDeployments } from '../DeviceDeployments';

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
  get.mockReset().mockResolvedValue({ data: { deployments: [NEWEST, OLDEST] } });
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
});
