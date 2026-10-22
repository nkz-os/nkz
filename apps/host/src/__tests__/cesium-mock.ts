import { vi } from 'vitest';

export const Rectangle = {
  fromDegrees: vi.fn((w: number, s: number, e: number, n: number) => ({ west: w, south: s, east: e, north: n })),
};

export const Cartesian3 = {
  fromDegrees: vi.fn((lon: number, lat: number, h: number) => ({ x: lon, y: lat, z: h })),
};

export const BoundingSphere = {
  fromPoints: vi.fn((pts: unknown[]) => ({ center: pts[0] ?? null, radius: pts.length * 100 })),
};

const Camera = vi.fn(() => ({
  flyTo: vi.fn(),
  flyToBoundingSphere: vi.fn(),
}));

export const Viewer = vi.fn(() => ({
  camera: new (Camera as any)(),
})) as unknown as { new(): { camera: { flyTo: any; flyToBoundingSphere: any } } };
