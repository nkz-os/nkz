import { describe, it, expect } from 'vitest';
import { terrainProviderForRegion, imageryProviderForRegion } from '../terrain';

describe('terrainProviderForRegion', () => {
  it('navarra → idena (MDT05 5m)', () => {
    expect(terrainProviderForRegion('navarra')).toBe('idena');
  });

  it('spain → ign', () => {
    expect(terrainProviderForRegion('spain')).toBe('ign');
  });

  it('eu → eu (eu-elevation module)', () => {
    expect(terrainProviderForRegion('eu')).toBe('eu');
  });

  it('world → eu (eu-elevation module fallback)', () => {
    expect(terrainProviderForRegion('world')).toBe('eu');
  });
});

describe('imageryProviderForRegion', () => {
  it('navarra → pnoa', () => {
    expect(imageryProviderForRegion('navarra')).toBe('pnoa');
  });

  it('spain → pnoa', () => {
    expect(imageryProviderForRegion('spain')).toBe('pnoa');
  });

  it('eu → esri', () => {
    expect(imageryProviderForRegion('eu')).toBe('esri');
  });

  it('world → esri', () => {
    expect(imageryProviderForRegion('world')).toBe('esri');
  });
});
