import { render, screen, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { CapabilityValue } from '../CapabilityValue';

const mockFetch = (body: unknown, status = 200) =>
  vi.fn().mockResolvedValue({ ok: status === 200, status, json: async () => body });

describe('<CapabilityValue>', () => {
  beforeEach(() => {
    // reset between tests
    (globalThis as any).fetch = undefined;
  });

  it('renders value and provenance when entity exists', async () => {
    (globalThis as any).fetch = mockFetch([{
      id: 'urn:ngsi-ld:AgriSoilExtended:p-1',
      type: 'AgriSoilExtended',
      organicCarbon: {
        type: 'Property',
        value: 1.8,
        providedBy: { value: 'LUCAS-2018' },
        license: { value: 'JRC-LUCAS-2018' },
      },
    }]);
    render(<CapabilityValue parcelId="p-1" entityType="AgriSoilExtended" attribute="organicCarbon" />);
    await waitFor(() => expect(screen.getByText(/1\.8/)).toBeInTheDocument());
    expect(screen.getByText(/LUCAS-2018/)).toBeInTheDocument();
    expect(screen.getByText(/JRC-LUCAS-2018/i)).toBeInTheDocument();
  });

  it('renders loading state initially', () => {
    (globalThis as any).fetch = vi.fn(() => new Promise(() => {}));
    render(<CapabilityValue parcelId="p-1" entityType="AgriSoilExtended" attribute="organicCarbon" />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it('renders no-data state when attribute is absent', async () => {
    (globalThis as any).fetch = mockFetch([{ id: 'x', type: 'AgriSoilExtended' }]);
    render(<CapabilityValue parcelId="p-1" entityType="AgriSoilExtended" attribute="organicCarbon" />);
    await waitFor(() => expect(screen.getByText(/no data/i)).toBeInTheDocument());
  });

  it('renders no-entitlement state when 403 is returned', async () => {
    (globalThis as any).fetch = mockFetch({ detail: 'no entitlement' }, 403);
    render(<CapabilityValue parcelId="p-1" entityType="AgriSoilExtended" attribute="organicCarbon" />);
    await waitFor(() => expect(screen.getByText(/entitlement required/i)).toBeInTheDocument());
  });
});
