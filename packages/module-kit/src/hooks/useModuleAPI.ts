import { useQuery, useMutation } from '@tanstack/react-query';
import { useNKZRuntime } from '../runtime/NKZContext';
import type { QueryResult } from './types';

export function useGet<T = unknown>(path: string, opts?: { enabled?: boolean }): QueryResult<T> {
  const { moduleApi, moduleId } = useNKZRuntime();
  const q = useQuery({
    queryKey: ['nkz:moduleApi', moduleId, 'GET', path],
    queryFn: () => moduleApi.fetch<T>(path),
    enabled: opts?.enabled !== false,
  });
  return {
    data: q.data,
    isLoading: q.isLoading,
    isFetching: q.isFetching,
    error: q.error,
    refetch: q.refetch,
  };
}

export function usePost<TResponse = unknown, TBody = unknown>(path: string) {
  const { moduleApi } = useNKZRuntime();
  return useMutation({
    mutationFn: (body: TBody) =>
      moduleApi.fetch<TResponse>(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
  });
}

export function usePatch<TResponse = unknown, TBody = unknown>(path: string) {
  const { moduleApi } = useNKZRuntime();
  return useMutation({
    mutationFn: (body: TBody) =>
      moduleApi.fetch<TResponse>(path, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
  });
}

export function useDelete<TResponse = unknown>(path: string) {
  const { moduleApi } = useNKZRuntime();
  return useMutation({
    mutationFn: () => moduleApi.fetch<TResponse>(path, { method: 'DELETE' }),
  });
}
