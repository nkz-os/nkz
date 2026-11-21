import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { observations, ObservationsError, ObservationsParseError } from '../src/api/observations';
import { apiOrigin, toApiUrl } from '../src/api/apiOrigin';

type Env = { __ENV__?: unknown };
const win = window as unknown as Env;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const obs = (t: string, extra: Record<string, unknown> = {}) => ({
  t,
  value: 1.5,
  observedProperty: 'airTemperature',
  deploymentId: 'dep-1',
  calibrated: false,
  ...extra,
});

const FROM = new Date('2026-11-01T00:00:00Z');
const TO = '2026-11-02T00:00:00Z';

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  win.__ENV__ = { VITE_API_URL: 'https://api.example.test/' };
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  delete win.__ENV__;
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('apiOrigin copy', () => {
  it('strips trailing slash and only rewrites root-relative paths', () => {
    expect(apiOrigin()).toBe('https://api.example.test');
    expect(toApiUrl('/api/x')).toBe('https://api.example.test/api/x');
    expect(toApiUrl('//evil/x')).toBe('//evil/x');
    expect(toApiUrl('https://o.test/x')).toBe('https://o.test/x');
  });
});

describe('observations client', () => {
  it('uses runtime origin from window.__ENV__', async () => {
    fetchMock.mockResolvedValue(json({ items: [] }));
    await observations.properties();
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/api/observations/properties');
  });

  it('sends credentials include', async () => {
    fetchMock.mockResolvedValue(json({ items: [] }));
    await observations.properties();
    expect(fetchMock.mock.calls[0][1].credentials).toBe('include');
  });

  it('best posts the camelCase body with Z dates and parses the result', async () => {
    fetchMock.mockResolvedValue(
      json({
        feature: 'urn:f', property: 'airTemperature', unitCode: 'CEL', unitSymbol: '°C',
        medium: 'ambientAir', mode: 'series', resolution: 'PT1H', tz: 'UTC',
        from: '2026-11-01T00:00:00Z', to: TO,
        groups: [{
          subContext: { heightM: 2 },
          points: [
            { t: '2026-11-01T00:00:00Z', value: 9.4, tier: 2, fidelity: 'iot_sensor',
              confidence: 'high', calibrated: true, mediumFallback: false, coverage: 0.9,
              sources: [{ kind: 'device', id: 'urn:d', deploymentId: 'u', distanceM: null, n: 3 }] },
            { t: '2026-11-01T01:00:00Z', value: null, tier: null, fidelity: 'unavailable',
              confidence: 'none', calibrated: false, mediumFallback: false, coverage: 0,
              sources: [{ kind: 'model', id: 'parcel_weather' }, { kind: 'device', id: null }] },
          ],
        }],
        tiersUsed: [2], warnings: [],
      }),
    );
    const r = await observations.best({
      feature: 'urn:f', property: 'airTemperature', from: FROM, to: TO,
      subContext: { height: 2 },
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://api.example.test/api/observations/best');
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body);
    expect(body).toMatchObject({
      feature: 'urn:f', property: 'airTemperature', from: '2026-11-01T00:00:00Z', to: TO,
      mode: 'series', resolution: 'PT1H', tz: 'UTC', tiers: 'all',
      allowMediumFallback: false, subContext: { height: 2 },
    });
    expect('geometry' in body).toBe(false);
    expect(r.groups[0].subContext.heightM).toBe(2);
    expect(r.groups[0].points[0].sources[0].deploymentId).toBe('u');
    const model = r.groups[0].points[1].sources[0];
    expect(model.deploymentId).toBeUndefined();
    expect(r.groups[0].points[1].sources[1].id).toBeNull();
    expect(r.tiersUsed).toEqual([2]);
  });

  it('best rejects both or neither of feature/geometry', async () => {
    await expect(
      observations.best({ property: 'p', from: FROM, to: TO }),
    ).rejects.toThrow(/exactly one/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('latest sends mode latest with a nominal from<to window', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-20T12:00:00Z'));
    fetchMock.mockResolvedValue(
      json({ feature: 'urn:f', property: 'p', medium: 'm', mode: 'latest', resolution: 'PT1H',
             tz: 'UTC', from: '2026-11-20T11:00:00Z', to: '2026-11-20T12:00:00Z',
             groups: [], tiersUsed: [], warnings: [] }),
    );
    await observations.latest({ feature: 'urn:f', property: 'p' });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.mode).toBe('latest');
    expect(body.from).toBe('2026-11-20T11:00:00Z');
    expect(body.to).toBe('2026-11-20T12:00:00Z');
  });

  it('raw iterates across pages, following a cursor through short and empty pages', async () => {
    fetchMock
      .mockResolvedValueOnce(json({ items: [obs('2026-11-01T00:00:00Z')], nextCursor: 'c1' }))
      .mockResolvedValueOnce(json({ items: [], nextCursor: 'c2' }))
      .mockResolvedValueOnce(json({ items: null, nextCursor: 'c3' }))
      .mockResolvedValueOnce(
        json({ items: [obs('2026-11-01T01:00:00Z', { location: { type: 'Point', coordinates: [1, 2] }, heightM: 2 })], nextCursor: null }),
      );
    const out = [];
    for await (const o of observations.raw({ feature: 'urn:f', property: 'p', from: FROM, to: TO, limit: 5, qualityFlags: ['ok'] })) {
      out.push(o);
    }
    expect(out.map((o) => o.t)).toEqual(['2026-11-01T00:00:00Z', '2026-11-01T01:00:00Z']);
    expect(out[1].heightM).toBe(2);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    const b0 = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect('cursor' in b0).toBe(false);
    expect(b0.limit).toBe(5);
    expect(b0.qualityFlags).toEqual(['ok']);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).cursor).toBe('c1');
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/api/observations');
  });

  it('raw aborts on a repeated cursor (A to B to A)', async () => {
    fetchMock
      .mockResolvedValueOnce(json({ items: [], nextCursor: 'A' }))
      .mockResolvedValueOnce(json({ items: [], nextCursor: 'B' }))
      .mockResolvedValueOnce(json({ items: [], nextCursor: 'A' }));
    const run = async () => {
      for await (const _ of observations.raw({ feature: 'f', property: 'p', from: FROM, to: TO })) void _;
    };
    await expect(run()).rejects.toBeInstanceOf(ObservationsParseError);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('error carries status', async () => {
    fetchMock.mockResolvedValue(json({ error: 'unknown property' }, 422));
    const err = await observations
      .best({ feature: 'f', property: 'p', from: FROM, to: TO })
      .catch((e) => e);
    expect(err).toBeInstanceOf(ObservationsError);
    expect(err.status).toBe(422);
    expect(err.message).toBe('unknown property');
  });

  it('non-JSON error body falls back to text and status', async () => {
    fetchMock.mockResolvedValue(new Response('upstream down', { status: 502 }));
    const err = await observations.properties().catch((e) => e);
    expect(err.status).toBe(502);
    expect(err.message).toBe('upstream down');
  });

  it('2xx with a non-JSON or malformed body raises a typed parse error', async () => {
    fetchMock.mockResolvedValueOnce(new Response('<html>', { status: 200 }));
    const e1 = await observations.properties().catch((e) => e);
    expect(e1).toBeInstanceOf(ObservationsParseError);
    expect(e1).toBeInstanceOf(ObservationsError);
    expect(e1.status).toBe(502);

    fetchMock.mockResolvedValueOnce(json({ groups: 'nope' }));
    const e2 = await observations
      .best({ feature: 'f', property: 'p', from: FROM, to: TO })
      .catch((e) => e);
    expect(e2).toBeInstanceOf(ObservationsParseError);
  });

  it('properties parses {items} and tolerates null items', async () => {
    fetchMock.mockResolvedValueOnce(
      json({ items: [{ id: 'airTemperature', tenantId: null, name: 'airTemperature', domain: 'weather', defaultMedium: 'ambientAir', temporalSemantics: 'instant', unit: '°C', unitCode: 'CEL', allowedMedia: ['ambientAir'], hasModel: true }] }),
    );
    const props = await observations.properties();
    expect(props[0].unitCode).toBe('CEL');
    expect(props[0].allowedMedia).toEqual(['ambientAir']);
    fetchMock.mockResolvedValueOnce(json({ items: null }));
    expect(await observations.properties()).toEqual([]);
  });

  it('rejects naive/invalid dates', async () => {
    await expect(
      observations.best({ feature: 'f', property: 'p', from: '2026-11-01T00:00:00', to: TO }),
    ).rejects.toThrow(/timezone/);
  });
});
