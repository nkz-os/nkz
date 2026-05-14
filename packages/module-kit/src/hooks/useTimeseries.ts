import { useQuery } from '@tanstack/react-query';
import { useNKZRuntime } from '../runtime/NKZContext';
import type { QueryResult, TimeseriesPoint, TimeseriesQuery } from './types';

/**
 * Time-series hook backed by the platform timeseries-reader. The dev passes
 * `entityId`, `attribute`, and a time window; the SDK builds the request,
 * normalises the response into `[{timestamp, value}]`, and caches via
 * TanStack Query.
 *
 * @example
 *   const { data, isLoading } = useTimeseries({
 *     entityId: 'urn:ngsi-ld:WeatherObserved:station-1',
 *     attribute: 'temperature',
 *     from: new Date(Date.now() - 7 * 86400_000),
 *     to: new Date(),
 *     resolution: 200,
 *   });
 */
export function useTimeseries(
  opts: TimeseriesQuery & { enabled?: boolean },
): QueryResult<TimeseriesPoint[]> {
  const { timeseries } = useNKZRuntime();
  const fromKey = typeof opts.from === 'string' ? opts.from : opts.from.toISOString();
  const toKey = typeof opts.to === 'string' ? opts.to : opts.to.toISOString();
  const q = useQuery({
    queryKey: [
      'nkz:timeseries',
      opts.entityId,
      opts.attribute,
      fromKey,
      toKey,
      opts.resolution ?? null,
    ] as const,
    queryFn: () => timeseries.query(opts),
    enabled: opts.enabled !== false,
  });
  return {
    data: q.data,
    isLoading: q.isLoading,
    isFetching: q.isFetching,
    error: q.error,
    refetch: q.refetch,
  };
}
