import React, { ReactNode } from 'react';
import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { MockProvider } from '../src/mock/MockProvider';
import { useFiles } from '../src/hooks/useFiles';

const wrapper = ({ children }: { children: ReactNode }) => (
  <MockProvider fixtures={{ moduleId: 'test' }}>{children}</MockProvider>
);

describe('useFiles (mock)', () => {
  it('upload + getUrl roundtrip', async () => {
    const { result } = renderHook(() => useFiles(), { wrapper });
    const blob = new Blob(['hello'], { type: 'text/plain' });
    const { url } = await result.current.upload(blob, 'docs/hello.txt');
    expect(url).toBe('mock://files/docs/hello.txt');
    expect(await result.current.getUrl('docs/hello.txt')).toBe('mock://files/docs/hello.txt');
  });

  it('list returns uploaded paths under the prefix', async () => {
    const { result } = renderHook(() => useFiles(), { wrapper });
    await result.current.upload(new Blob(['a']), 'reports/a.txt');
    await result.current.upload(new Blob(['b']), 'reports/b.txt');
    await result.current.upload(new Blob(['c']), 'other/c.txt');
    const items = await result.current.list('reports/');
    expect(items.sort()).toEqual(['reports/a.txt', 'reports/b.txt']);
  });

  it('getUrl throws on missing file', async () => {
    const { result } = renderHook(() => useFiles(), { wrapper });
    await expect(result.current.getUrl('nope/x.txt')).rejects.toThrow(/not found/);
  });
});
