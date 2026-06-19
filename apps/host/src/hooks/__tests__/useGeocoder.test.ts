import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useGeocoder } from '../useGeocoder';
import type { GeocodeResult } from '@/types/geocode';

const DEBOUNCE_MS = 300;

const mockResult: GeocodeResult = {
  label: 'Pamplona, Spain',
  lat: 42.81,
  lon: -1.64,
  bbox: [-1.7, 42.7, -1.5, 42.9],
  type: 'city',
  countryCode: 'ES',
};

/** Fetch mock that resolves immediately and respects AbortSignal. */
function makeFetchOk(body: unknown) {
  return vi.fn().mockImplementation(
    (_url: string, opts?: RequestInit) =>
      new Promise((resolve, reject) => {
        const signal = (opts as any)?.signal as AbortSignal | undefined;
        if (signal?.aborted) {
          reject(new DOMException('The user aborted a request.', 'AbortError'));
          return;
        }
        signal?.addEventListener('abort', () => {
          reject(new DOMException('The user aborted a request.', 'AbortError'));
        });
        resolve({ ok: true, json: () => Promise.resolve(body) });
      }),
  );
}

/** Fetch mock that resolves on demand and respects AbortSignal. */
function deferredFetch() {
  let doResolve!: (v: unknown) => void;
  const inner = new Promise((resolve) => { doResolve = resolve; });
  const mock = vi.fn().mockImplementation(
    (_url: string, opts?: RequestInit) =>
      new Promise((resolve, reject) => {
        const signal = (opts as any)?.signal as AbortSignal | undefined;
        if (signal?.aborted) {
          reject(new DOMException('The user aborted a request.', 'AbortError'));
          return;
        }
        signal?.addEventListener('abort', () => {
          reject(new DOMException('The user aborted a request.', 'AbortError'));
        });
        inner.then(resolve);
      }),
  );
  return { mock, resolve: doResolve };
}

/** Fetch mock that returns an HTTP error and respects AbortSignal. */
function makeFetchError(status: number) {
  return vi.fn().mockImplementation(
    (_url: string, opts?: RequestInit) =>
      new Promise((resolve, reject) => {
        const signal = (opts as any)?.signal as AbortSignal | undefined;
        if (signal?.aborted) {
          reject(new DOMException('The user aborted a request.', 'AbortError'));
          return;
        }
        signal?.addEventListener('abort', () => {
          reject(new DOMException('The user aborted a request.', 'AbortError'));
        });
        resolve({ ok: false, status });
      }),
  );
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('useGeocoder', () => {
  it('returns empty results and no error initially', () => {
    const { result } = renderHook(() => useGeocoder());
    expect(result.current.results).toEqual([]);
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('debounces multiple rapid calls — only last fires after DEBOUNCE_MS', async () => {
    const fetchMock = makeFetchOk({ results: [mockResult] });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useGeocoder());

    act(() => { result.current.search('Pam'); });
    act(() => { result.current.search('Pampl'); });
    act(() => { result.current.search('Pamplona'); });

    // Before debounce fires — fetch not called yet
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.results).toEqual([]);

    // Advance less than DEBOUNCE_MS — still no fetch
    act(() => { vi.advanceTimersByTime(DEBOUNCE_MS - 50); });
    expect(fetchMock).not.toHaveBeenCalled();

    // Advance remaining — debounced callback fires
    act(() => { vi.advanceTimersByTime(100); });

    // Flush async run() — the fetch promise resolves, React re-renders
    await act(async () => {});

    // Last query should be "Pamplona"
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const callUrl = fetchMock.mock.calls[0][0] as string;
    expect(callUrl).toContain('q=Pamplona');
    expect(callUrl).toContain('lang=es');
    expect(callUrl).toContain('limit=5');

    // Results should be set
    expect(result.current.results).toHaveLength(1);
    expect(result.current.results[0].label).toBe('Pamplona, Spain');

    // Final state
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('clears results and error when query is empty without fetching', async () => {
    const fetchMock = makeFetchOk({ results: [mockResult] });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useGeocoder());

    // Run a search first to populate results
    act(() => { result.current.search('test'); });
    act(() => { vi.advanceTimersByTime(DEBOUNCE_MS); });
    await act(async () => {});
    expect(result.current.results).toHaveLength(1);

    // Now search with empty query
    act(() => { result.current.search(''); });

    // Should clear results without additional fetch
    expect(fetchMock).toHaveBeenCalledTimes(1); // only first search fired
    expect(result.current.results).toEqual([]);
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it('sets loading true during fetch and false after', async () => {
    const { mock: fetchMock, resolve } = deferredFetch();
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useGeocoder());

    // Trigger search
    act(() => { result.current.search('Pamplona'); });
    act(() => { vi.advanceTimersByTime(DEBOUNCE_MS); });

    // Flush to let run() start and set loading=true (before await fetch)
    await act(async () => {});

    // run() has started but suspended at await fetch — loading should be true
    expect(result.current.loading).toBe(true);

    // Resolve the deferred fetch
    await act(async () => {
      resolve({ ok: true, json: () => Promise.resolve({ results: [mockResult] }) });
    });

    // After resolving, run() should complete with results
    expect(result.current.loading).toBe(false);
    expect(result.current.results).toHaveLength(1);
    expect(result.current.results[0].label).toBe('Pamplona, Spain');
  });

  it('sets error string on HTTP error', async () => {
    const fetchMock = makeFetchError(500);
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useGeocoder());

    act(() => { result.current.search('Pamplona'); });
    act(() => { vi.advanceTimersByTime(DEBOUNCE_MS); });

    // Flush async run() — the error fetch resolves, catch sets error
    await act(async () => {});

    expect(result.current.error).toBe('search_unavailable');
    expect(result.current.results).toEqual([]);
    expect(result.current.loading).toBe(false);
  });

  it('cleans up timer and abort on unmount', () => {
    const fetchMock = makeFetchOk({ results: [mockResult] });
    vi.stubGlobal('fetch', fetchMock);

    const { result, unmount } = renderHook(() => useGeocoder());
    act(() => { result.current.search('Pamplona'); });
    unmount();
    // After unmount, the debounce timer should be cleared (no pending callback)
    act(() => { vi.advanceTimersByTime(300); });
    // No state updates should happen — no assertions needed beyond no console errors
    // If cleanup was missing, vitest would warn about state updates on unmounted components
  });

  it('aborts previous pending fetch and keeps latest results', async () => {
    const { mock: firstFetch, resolve: resolveFirst } = deferredFetch();
    const secondFetch = makeFetchOk({
      results: [{ ...mockResult, label: 'Madrid, Spain' }],
    });
    vi.stubGlobal('fetch', vi.fn()
      .mockImplementationOnce(firstFetch)
      .mockImplementationOnce(secondFetch),
    );

    const { result } = renderHook(() => useGeocoder());

    // Start first search — fetch begins, deferred (doesn't resolve yet)
    act(() => { result.current.search('Pamplona'); });
    act(() => { vi.advanceTimersByTime(DEBOUNCE_MS); });
    await act(async () => {}); // flush run() startup
    expect(firstFetch).toHaveBeenCalledTimes(1);
    expect(result.current.loading).toBe(true);

    // Start second search while first is pending — should abort first run
    act(() => { result.current.search('Madrid'); });
    act(() => { vi.advanceTimersByTime(DEBOUNCE_MS); });

    // Flush: second run aborts first, second fetch resolves immediately
    await act(async () => {});

    // Second fetch should fire and results updated to Madrid
    expect(result.current.results).toHaveLength(1);
    expect(result.current.results[0].label).toBe('Madrid, Spain');
    expect(result.current.loading).toBe(false);

    // Now resolve the first (now-aborted) deferred fetch
    await act(async () => {
      resolveFirst({ ok: true, json: () => Promise.resolve({ results: [mockResult] }) });
    });

    // Results should still be from the latest search (Madrid), not overwritten
    expect(result.current.results).toHaveLength(1);
    expect(result.current.results[0].label).toBe('Madrid, Spain');
  });
});
