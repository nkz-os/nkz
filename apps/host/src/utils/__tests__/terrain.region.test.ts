import { describe, it, expect } from 'vitest';
import { terrainProviderForRegion, imageryProviderForRegion } from '../terrain';

describe('terrainProviderForRegion', () => {
  it('navarra → idena (MDT05 5m)', () => {
    expect(terrainProviderForRegion('navarra')).toBe('idena');
  });

  it('spain → ign', () => {
    expect(terrainProviderForRegion('spain')).toBe('ign');
  });

  it('eu → terrarium (global open-data; eu-elevation module still wins via host guard)', () => {
    expect(terrainProviderForRegion('eu')).toBe('terrarium');
  });

  it('world → terrarium (global open-data fallback)', () => {
    expect(terrainProviderForRegion('world')).toBe('terrarium');
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
