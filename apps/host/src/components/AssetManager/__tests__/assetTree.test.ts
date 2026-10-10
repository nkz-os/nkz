import { describe, expect, it } from 'vitest';
import type { AssetCategory, UnifiedAsset } from '@/types/assets';
import { buildAssetTree, type AssetTreeNode } from '../assetTree';

function asset(id: string, category: AssetCategory, parentId?: string, name = id, type = id): UnifiedAsset {
  return { id, type, name, category, status: 'unknown', hasLocation: false, parentId, rawEntity: {} };
}

const shape = (nodes: AssetTreeNode[]): unknown =>
  nodes.map(n => (n.children.length ? { [n.asset.id]: shape(n.children) } : n.asset.id));

describe('buildAssetTree', () => {
  it('nests children under the parent they name', () => {
    const tree = buildAssetTree([
      asset('parcel', 'parcels'),
      asset('crop', 'vegetation', 'parcel'),
      asset('station', 'weather', 'parcel'),
    ]);
    expect(shape(tree)).toEqual([{ parcel: ['crop', 'station'] }]);
    expect(tree[0].childCount).toBe(2);
  });

  it('keeps an asset whose parent is not loaded at root', () => {
    expect(shape(buildAssetTree([asset('sensor', 'sensors', 'missing-parcel')]))).toEqual(['sensor']);
  });

  it('keeps every asset of a cycle at root instead of dropping it', () => {
    const tree = buildAssetTree([asset('a', 'sensors', 'b'), asset('b', 'sensors', 'a')]);
    expect(shape(tree)).toEqual(['a', 'b']);
  });

  it('keeps every asset of a longer cycle at root', () => {
    const tree = buildAssetTree([
      asset('a', 'sensors', 'c'),
      asset('b', 'sensors', 'a'),
      asset('c', 'sensors', 'b'),
    ]);
    expect(shape(tree)).toEqual(['a', 'b', 'c']);
  });

  it('lists parcels first at root, then by name', () => {
    const tree = buildAssetTree([
      asset('robot', 'fleet', undefined, 'Alpha'),
      asset('p2', 'parcels', undefined, 'Zeta'),
      asset('p1', 'parcels', undefined, 'Beta'),
    ]);
    expect(shape(tree)).toEqual(['p1', 'p2', 'robot']);
  });

  it('orders children by type: zones, crops, sensors, weather, then the rest, each by name', () => {
    const tree = buildAssetTree([
      asset('parcel', 'parcels'),
      asset('tank', 'water', 'parcel', 'A tank'),
      asset('station', 'weather', 'parcel', 'A station'),
      asset('sensor-b', 'sensors', 'parcel', 'B sensor'),
      asset('sensor-a', 'sensors', 'parcel', 'A sensor'),
      asset('crop', 'vegetation', 'parcel', 'Z crop'),
      asset('zone', 'parcels', 'parcel', 'Z zone'),
    ]);
    expect(shape(tree)).toEqual([
      { parcel: ['zone', 'crop', 'sensor-a', 'sensor-b', 'station', 'tank'] },
    ]);
  });
});
