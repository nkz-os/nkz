import { useCallback, useRef, useState } from 'react';
import { GeocodeResult, isGeocodeResult } from '@/types/geocode';

const API = (import.meta as any)?.env?.VITE_API_URL || 'https://nkz.robotika.cloud';
const DEBOUNCE_MS = 300;

export function useGeocoder(lang = 'es') {
  const [results, setResults] = useState<GeocodeResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abort = useRef<AbortController | null>(null);

  const run = useCallback(async (q: string) => {
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    setLoading(true);
    setError(null);
    try {
      const url = `${API}/api/geocode?q=${encodeURIComponent(q)}&lang=${lang}&limit=5`;
      const r = await fetch(url, { credentials: 'include', signal: ctrl.signal });
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
  }, [lang]);

  const search = useCallback((q: string) => {
    if (timer.current) clearTimeout(timer.current);
    if (!q.trim()) {
      setResults([]);
      setError(null);
      return;
    }
    timer.current = setTimeout(() => run(q.trim()), DEBOUNCE_MS);
  }, [run]);

  return { results, loading, error, search };
}
