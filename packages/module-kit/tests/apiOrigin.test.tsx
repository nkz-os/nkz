import React, { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { NKZProvider } from '../src/runtime/NKZProvider';
import { useEntities } from '../src/hooks/useOrion';
import { useGet } from '../src/hooks/useModuleAPI';
import { useAPI } from '../src/hooks/useAPI';

// The session cookie is host-only on the API host: a request to the frontend
// host carries no credentials (401). Every gateway-bound call must go to the
// runtime API origin with credentials.
const wrapper = ({ children }: { children: ReactNode }) => (
  <NKZProvider moduleId="soil" apiBasePath="/api/soil">{children}</NKZProvider>
);

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as { __ENV__?: unknown }).__ENV__;
});

describe('API origin', () => {
  it('lists entities on the API origin with credentials and the module id', async () => {
    (window as { __ENV__?: unknown }).__ENV__ = { VITE_API_URL: 'https://api.example.test/' };
    const { result } = renderHook(() => useEntities('AgriParcel'), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://api.example.test/ngsi-ld/v1/entities?type=AgriParcel');
    expect(init.credentials).toBe('include');
    expect(init.headers['X-Module-Id']).toBe('soil');
  });

  it('sends module API calls to the API origin too', async () => {
    (window as { __ENV__?: unknown }).__ENV__ = { VITE_API_URL: 'https://api.example.test' };
    const { result } = renderHook(() => useGet('/parcels'), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/api/soil/parcels');
  });

  it('points the preconfigured client at the API origin', async () => {
    (window as { __ENV__?: unknown }).__ENV__ = { VITE_API_URL: 'https://api.example.test' };
    const { result } = renderHook(() => useAPI(), { wrapper });
    await result.current.request('/parcels');
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/api/soil/parcels');
  });

  it('keeps same-origin paths when no API origin is configured', async () => {
    const { result } = renderHook(() => useEntities('AgriParcel'), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(fetchMock.mock.calls[0][0]).toBe('/ngsi-ld/v1/entities?type=AgriParcel');
  });
});
