/**
 * Subparcels: the parent travels to the server as parentId and reads back from the alias.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock('axios', () => {
  const client = {
    get,
    post,
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  };
  return { default: { create: () => client }, create: () => client };
});

import { parcelApi } from '../parcelApi';

const PARENT = 'urn:ngsi-ld:AgriParcel:p-1';
const geometry = { type: 'Polygon', coordinates: [] } as any;

describe('parcelApi subparcels', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    post.mockResolvedValue({ data: { id: 'urn:ngsi-ld:AgriParcel:p-2' } });
  });

  it('sends the parent of a subparcel as parentId', async () => {
    await parcelApi.createParcel({ geometry, refParent: PARENT });
    expect(post.mock.calls[0][1].parentId).toBe(PARENT);
  });

  it('sends no parent for a parcel of its own', async () => {
    await parcelApi.createParcel({ geometry });
    expect(post.mock.calls[0][1]).not.toHaveProperty('parentId');
  });

  it('sends the parent of a management zone as parentId', async () => {
    await parcelApi.createZones({ id: PARENT } as any, [{ geometry }]);
    const body = post.mock.calls[0][1];
    expect(body.parentId).toBe(PARENT);
    expect(body).not.toHaveProperty('refParent');
  });

  it('reads the parent link the broker returns under its alias', async () => {
    get.mockResolvedValue({
      data: [{
        id: 'urn:ngsi-ld:AgriParcel:p-2',
        type: 'AgriParcel',
        location: { type: 'GeoProperty', value: geometry },
        refAgriParcel: { type: 'Relationship', object: PARENT },
      }],
    });
    const [parcel] = await parcelApi.getParcels();
    expect(parcel.refParent).toBe(PARENT);
  });
});
