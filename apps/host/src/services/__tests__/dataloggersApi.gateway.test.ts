import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../api', () => ({ default: http }));
import { getDataloggerConfig, listDataloggers, parseBeacon, parseGateway } from '../dataloggersApi';

const URN = 'urn:ngsi-ld:Device:tenant-a:gw-0123456789ab';
const item = (extra: Record<string, unknown> = {}) => ({
  id: 'dl-1', name: 'A', kind: 'datak', username: 'u', status: 'active', devices: [], ...extra,
});

beforeEach(() => http.get.mockReset());

describe('parseGateway', () => {
  it('keeps the documented shape', () => {
    const g = { urn: URN, lastSeen: '2026-11-26T10:00:00Z', rssi: -71, softwareVersion: '1.2.3' };
    expect(parseGateway(g)).toEqual(g);
  });
  it('maps missing or mistyped members to null', () => {
    expect(parseGateway({ urn: URN, lastSeen: 5, rssi: '-70', softwareVersion: '' }))
      .toEqual({ urn: URN, lastSeen: null, rssi: null, softwareVersion: null });
    expect(parseGateway({ urn: URN })).toEqual({ urn: URN, lastSeen: null, rssi: null, softwareVersion: null });
  });
  it.each([null, undefined, 'x', 3, {}, { urn: 4 }, { lastSeen: '2026-11-26T10:00:00Z' }])('is null for %j', (raw) => {
    expect(parseGateway(raw)).toBeNull();
  });
});

describe('parseBeacon', () => {
  it('needs a topic and a numeric interval', () => {
    expect(parseBeacon({ topic: '/k/gw-0123456789ab/attrs', intervalSeconds: 300 }))
      .toEqual({ topic: '/k/gw-0123456789ab/attrs', intervalSeconds: 300 });
    expect(parseBeacon({ topic: '/k/x/attrs' })).toBeUndefined();
    expect(parseBeacon({ topic: '', intervalSeconds: 300 })).toBeUndefined();
    expect(parseBeacon(null)).toBeUndefined();
  });
});

describe('listDataloggers', () => {
  it('gives every item a gateway: parsed, or null when the server sends none', async () => {
    http.get.mockResolvedValue({ data: [
      item({ gateway: { urn: URN, lastSeen: null, rssi: null, softwareVersion: null } }),
      item({ id: 'dl-2', gateway: null }),
      item({ id: 'dl-3' }),
    ] });
    const list = await listDataloggers();
    expect(list.map((d) => d.gateway)).toEqual([
      { urn: URN, lastSeen: null, rssi: null, softwareVersion: null }, null, null,
    ]);
    expect(list[1].id).toBe('dl-2');
  });
  it('is an empty list for a non-array answer', async () => {
    http.get.mockResolvedValue({ data: { error: 'x' } });
    expect(await listDataloggers()).toEqual([]);
  });
});

describe('getDataloggerConfig', () => {
  const base = { datalogger_id: 'dl-1', name: 'A', mqtt: { host: 'h' }, devices: [] };
  it('carries the beacon when present and valid', async () => {
    http.get.mockResolvedValue({ data: { ...base, beacon: { topic: '/k/gw-0123456789ab/attrs', intervalSeconds: 300 } } });
    expect((await getDataloggerConfig('dl-1')).beacon).toEqual({ topic: '/k/gw-0123456789ab/attrs', intervalSeconds: 300 });
  });
  it('has no beacon key otherwise, and the rest is untouched', async () => {
    http.get.mockResolvedValue({ data: { ...base, beacon: { topic: 5 } } });
    const config = await getDataloggerConfig('dl-1');
    expect(config).toEqual(base);
    expect('beacon' in config).toBe(false);
  });
});
