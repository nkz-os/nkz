import React, { ReactNode } from 'react';
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { MockProvider } from '../src/mock/MockProvider';
import { useTimeseries } from '../src/hooks/useTimeseries';

const wrapper = ({ children }: { children: ReactNode }) => (
  <MockProvider fixtures={{ moduleId: 'test' }}>{children}</MockProvider>
);

describe('useTimeseries (mock)', () => {
  it('returns synthetic data when nothing is seeded', async () => {
    const from = new Date('2026-01-01T00:00:00Z');
    const to = new Date('2026-01-02T00:00:00Z');
    const { result } = renderHook(
      () =>
        useTimeseries({
          entityId: 'urn:ngsi-ld:Sensor:1',
          attribute: 'temperature',
          from,
          to,
          resolution: 5,
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toHaveLength(5);
    expect(result.current.data?.[0]).toHaveProperty('timestamp');
    expect(typeof result.current.data?.[0].value).toBe('number');
  });

  it('accepts string from/to (ISO)', async () => {
    const { result } = renderHook(
      () =>
        useTimeseries({
          entityId: 'urn:s:2',
          attribute: 'humidity',
          from: '2026-01-01T00:00:00Z',
          to: '2026-01-02T00:00:00Z',
          resolution: 3,
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toHaveLength(3);
  });

  it('honours the enabled flag', async () => {
    const { result } = renderHook(
      () =>
        useTimeseries({
          entityId: 'urn:s:3',
          attribute: 'x',
          from: new Date(),
          to: new Date(),
          enabled: false,
        }),
      { wrapper },
    );
    // With enabled:false the query never runs — isLoading stays false and data undefined.
    expect(result.current.isLoading).toBe(false);
    expect(result.current.data).toBeUndefined();
  });
});
