import React, { ReactNode } from 'react';
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { MockProvider } from '../src/mock/MockProvider';
import {
  useEntity,
  useEntities,
  useCreateEntity,
  useUpdateEntity,
  useDeleteEntity,
} from '../src/hooks/useOrion';

const wrapWith =
  (entities: Array<{ id: string; type: string; [k: string]: unknown }>) =>
  ({ children }: { children: ReactNode }) => (
    <MockProvider fixtures={{ moduleId: 'test', entities }}>{children}</MockProvider>
  );

describe('useEntity', () => {
  it('loads a single entity by id', async () => {
    const wrapper = wrapWith([{ id: 'urn:p:1', type: 'AgriParcel', name: 'P1' }]);
    const { result } = renderHook(() => useEntity<{ id: string; type: string; name: string }>('urn:p:1'), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data?.name).toBe('P1');
  });

  it('reports error when entity is missing', async () => {
    const wrapper = wrapWith([]);
    const { result } = renderHook(() => useEntity('urn:p:404'), { wrapper });
    await waitFor(() => expect(result.current.error).not.toBeNull());
  });
});

describe('useEntities', () => {
  it('loads all entities of a type', async () => {
    const wrapper = wrapWith([
      { id: 'urn:p:1', type: 'AgriParcel', name: 'A' },
      { id: 'urn:p:2', type: 'AgriParcel', name: 'B' },
      { id: 'urn:f:1', type: 'AgriFarm', name: 'F' },
    ]);
    const { result } = renderHook(() => useEntities('AgriParcel'), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toHaveLength(2);
  });

  it('filters by a simple q expression', async () => {
    const wrapper = wrapWith([
      { id: 'urn:p:1', type: 'AgriParcel', category: 'vineyard' },
      { id: 'urn:p:2', type: 'AgriParcel', category: 'olive' },
    ]);
    const { result } = renderHook(() => useEntities('AgriParcel', { q: 'category=="vineyard"' }), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data?.[0].id).toBe('urn:p:1');
  });
});

describe('mutations', () => {
  // Mutations + queries must share the same provider (and therefore the same
  // QueryClient + Orion store) — co-render them in a single hook call.
  it('createEntity adds a new entity and invalidates list', async () => {
    const wrapper = wrapWith([{ id: 'urn:p:1', type: 'AgriParcel' }]);
    const { result } = renderHook(
      () => ({ list: useEntities('AgriParcel'), create: useCreateEntity() }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.list.isLoading).toBe(false));
    expect(result.current.list.data).toHaveLength(1);
    await result.current.create.mutateAsync({ id: 'urn:p:2', type: 'AgriParcel' });
    await waitFor(() => expect(result.current.list.data).toHaveLength(2));
  });

  it('updateEntity mutates and refreshes the entity query', async () => {
    const wrapper = wrapWith([{ id: 'urn:p:1', type: 'AgriParcel', name: 'old' }]);
    const { result } = renderHook(
      () => ({
        entity: useEntity<{ id: string; type: string; name: string }>('urn:p:1'),
        update: useUpdateEntity(),
      }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.entity.data?.name).toBe('old'));
    await result.current.update.mutateAsync({ id: 'urn:p:1', attrs: { name: 'new' } });
    await waitFor(() => expect(result.current.entity.data?.name).toBe('new'));
  });

  it('deleteEntity removes the entity from the list query', async () => {
    const wrapper = wrapWith([
      { id: 'urn:p:1', type: 'AgriParcel' },
      { id: 'urn:p:2', type: 'AgriParcel' },
    ]);
    const { result } = renderHook(
      () => ({ list: useEntities('AgriParcel'), del: useDeleteEntity() }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.list.data).toHaveLength(2));
    await result.current.del.mutateAsync({ id: 'urn:p:1' });
    await waitFor(() => expect(result.current.list.data).toHaveLength(1));
  });
});
