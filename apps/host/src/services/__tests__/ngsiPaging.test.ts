/**
 * Orion-LD answers a list call with a default page of 20 entities, silently.
 * `fetchAllPages` walks every page so the viewer never shows a truncated list.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

import { logger } from '@/utils/logger';
import { fetchAllPages, NGSI_MAX_PAGE, NGSI_MAX_ITEMS } from '../ngsiPaging';

/** A fake server holding `total` items; records every (offset, limit) asked for. */
function fakeServer(total: number) {
  const items = Array.from({ length: total }, (_, i) => ({ id: `e${i}` }));
  const calls: Array<[number, number]> = [];
  const fetchPage = vi.fn(async (offset: number, limit: number) => {
    calls.push([offset, limit]);
    return items.slice(offset, offset + limit);
  });
  return { items, calls, fetchPage };
}

describe('fetchAllPages', () => {
  beforeEach(() => {
    vi.mocked(logger.warn).mockClear();
  });

  it('exposes Orion-LD limits', () => {
    expect(NGSI_MAX_PAGE).toBe(1000);
    expect(NGSI_MAX_ITEMS).toBe(10_000);
  });

  it('one short page: one call, all items', async () => {
    const s = fakeServer(2);
    const out = await fetchAllPages(s.fetchPage, { pageSize: 3 });
    expect(out).toEqual(s.items);
    expect(s.calls).toEqual([[0, 3]]);
  });

  it('defaults to the Orion-LD maximum page size', async () => {
    const s = fakeServer(1);
    await fetchAllPages(s.fetchPage);
    expect(s.calls).toEqual([[0, NGSI_MAX_PAGE]]);
  });

  it('exactly pageSize items then an empty page: two calls', async () => {
    const s = fakeServer(3);
    const out = await fetchAllPages(s.fetchPage, { pageSize: 3 });
    expect(out).toEqual(s.items);
    expect(s.calls).toEqual([[0, 3], [3, 3]]);
  });

  it('2.5 pages: three calls at offsets 0, p and 2p, in order', async () => {
    const s = fakeServer(7);
    const out = await fetchAllPages(s.fetchPage, { pageSize: 3 });
    expect(out).toEqual(s.items);
    expect(s.calls).toEqual([[0, 3], [3, 3], [6, 3]]);
  });

  it('maxItems: trims the result and warns once, naming the label', async () => {
    const s = fakeServer(20);
    const out = await fetchAllPages(s.fetchPage, { pageSize: 3, maxItems: 7, label: 'AgriParcelRecord' });
    expect(out).toEqual(s.items.slice(0, 7));
    expect(s.calls).toEqual([[0, 3], [3, 3], [6, 3]]);
    expect(logger.warn).toHaveBeenCalledTimes(1);
    expect(String(vi.mocked(logger.warn).mock.calls[0].join(' '))).toContain('AgriParcelRecord');
  });

  it('rejects a non-positive pageSize instead of looping forever', async () => {
    const s = fakeServer(5);
    await expect(fetchAllPages(s.fetchPage, { pageSize: 0 })).rejects.toThrow(RangeError);
    expect(s.fetchPage).not.toHaveBeenCalled();
  });

  it('an error on page 2 rejects and is not swallowed', async () => {
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce([{ id: 'a' }, { id: 'b' }, { id: 'c' }])
      .mockRejectedValueOnce(new Error('boom'));
    await expect(fetchAllPages(fetchPage, { pageSize: 3 })).rejects.toThrow('boom');
    expect(fetchPage).toHaveBeenCalledTimes(2);
  });
});
