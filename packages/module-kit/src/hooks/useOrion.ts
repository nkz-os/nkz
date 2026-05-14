import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNKZRuntime } from '../runtime/NKZContext';
import type { NgsiLdEntity, QueryResult } from './types';

function entityKey(id: string): readonly unknown[] {
  return ['nkz:orion:entity', id];
}
function entitiesKey(type: string, opts?: { q?: string; limit?: number; offset?: number }): readonly unknown[] {
  return ['nkz:orion:entities', type, opts ?? {}];
}

export function useEntity<T extends NgsiLdEntity = NgsiLdEntity>(
  id: string,
  opts?: { type?: string; enabled?: boolean },
): QueryResult<T> {
  const { orion } = useNKZRuntime();
  const q = useQuery({
    queryKey: entityKey(id),
    queryFn: () => orion.getEntity(id, opts?.type) as Promise<T>,
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

export function useEntities<T extends NgsiLdEntity = NgsiLdEntity>(
  type: string,
  opts?: { q?: string; limit?: number; offset?: number; enabled?: boolean },
): QueryResult<T[]> {
  const { orion } = useNKZRuntime();
  const q = useQuery({
    queryKey: entitiesKey(type, { q: opts?.q, limit: opts?.limit, offset: opts?.offset }),
    queryFn: () => orion.listEntities(type, opts) as Promise<T[]>,
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

export function useCreateEntity() {
  const { orion } = useNKZRuntime();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (entity: NgsiLdEntity) => orion.createEntity(entity),
    onSuccess: (_, entity) => {
      qc.invalidateQueries({ queryKey: ['nkz:orion:entities', entity.type] });
    },
  });
}

export function useUpdateEntity() {
  const { orion } = useNKZRuntime();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, attrs }: { id: string; attrs: Record<string, unknown> }) =>
      orion.updateEntity(id, attrs),
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: entityKey(id) });
      qc.invalidateQueries({ queryKey: ['nkz:orion:entities'] });
    },
  });
}

export function useDeleteEntity() {
  const { orion } = useNKZRuntime();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) => orion.deleteEntity(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['nkz:orion:entities'] });
    },
  });
}
