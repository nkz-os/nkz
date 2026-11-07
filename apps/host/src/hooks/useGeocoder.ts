import { useCallback, useEffect, useRef, useState } from 'react';
import { GeocodeResult, isGeocodeResult } from '@/types/geocode';
import { useAuth } from '@/context/KeycloakAuthContext';
import { getConfig } from '@/config/environment';

const DEBOUNCE_MS = 300;
const MIN_QUERY = 2;

/** Search bounds covering the EU including outermost regions (Canarias, Azores, Madeira). */
export const EU_GEOCODE_BBOX = '-31,27,45,72';

export interface GeocoderOptions {
  /**
   * UI language forwarded to the geocoder. Only values the upstream supports
   * are sent; anything else (es, eu, …) is omitted so local official names
   * come back instead of an upstream 400.
   */
  lang?: string;
  /** Bias results towards the camera position at query time. */
  getCameraBias?: () => { lat: number; lon: number } | null;
}

function supportedLang(lang?: string): string | null {
  const l = (lang || '').toLowerCase();
  return l === 'en' || l === 'fr' || l === 'de' ? l : null;
}

export function useGeocoder({ lang, getCameraBias }: GeocoderOptions = {}) {
  const { getToken } = useAuth();
  const [results, setResults] = useState<GeocodeResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abort = useRef<AbortController | null>(null);

  // Runtime-first API base (window.__ENV__ injected by the host entrypoint,
  // falling back to the build-time value) — never a relative /api path.
  const apiBase = (getConfig().api.baseUrl || '').replace(/\/$/, '');

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
      abort.current?.abort();
    };
  }, []);

  const run = useCallback(async (q: string) => {
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    setLoading(true);
    setError(null);
    try {
      const token = getToken();
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const params = new URLSearchParams({
        q,
        limit: '6',
        bbox: EU_GEOCODE_BBOX,
      });
      const sLang = supportedLang(lang);
      if (sLang) params.set('lang', sLang);
      const bias = getCameraBias?.();
      if (bias) {
        params.set('lat', String(bias.lat));
        params.set('lon', String(bias.lon));
      }
      const r = await fetch(`${apiBase}/api/geocode?${params.toString()}`, {
        headers,
        credentials: 'include',
        signal: ctrl.signal,
      });
      if (!r.ok) throw new Error(`geocode ${r.status}`);
      const data: { results?: unknown[] } = await r.json();
      setResults(Array.isArray(data?.results) ? data.results.filter(isGeocodeResult) : []);
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        setError('search_unavailable');
        setResults([]);
      }
    } finally {
      setLoading(false);
    }
  }, [lang, getToken, apiBase, getCameraBias]);

  const search = useCallback((q: string) => {
    if (timer.current) clearTimeout(timer.current);
    if (q.trim().length < MIN_QUERY) {
      setResults([]);
      setError(null);
      return;
    }
    timer.current = setTimeout(() => run(q.trim()), DEBOUNCE_MS);
  }, [run]);

  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    abort.current?.abort();
    setResults([]);
    setError(null);
    setLoading(false);
  }, []);

  return { results, loading, error, search, clear };
}
