// =============================================================================
// Asset tree - hierarchy built from each asset's resolved parentId
// =============================================================================

import type { AssetCategory, UnifiedAsset } from '@/types/assets';

export interface AssetTreeNode {
  asset: UnifiedAsset;
  children: AssetTreeNode[];
  childCount: number;
}

/** Order of children under a parent: sub-parcels, crops, sensors, weather, then the rest. */
const CHILD_CATEGORY_ORDER: AssetCategory[] = [
  'parcels',
  'vegetation',
  'sensors',
  'weather',
  'water',
  'infrastructure',
  'fleet',
  'livestock',
];

function categoryRank(category: AssetCategory): number {
  const rank = CHILD_CATEGORY_ORDER.indexOf(category);
  return rank === -1 ? CHILD_CATEGORY_ORDER.length : rank;
}

function compareRoots(a: AssetTreeNode, b: AssetTreeNode): number {
  const aIsParcel = a.asset.category === 'parcels';
  const bIsParcel = b.asset.category === 'parcels';
  if (aIsParcel !== bIsParcel) return aIsParcel ? -1 : 1;
  return a.asset.name.localeCompare(b.asset.name);
}

function compareChildren(a: AssetTreeNode, b: AssetTreeNode): number {
  return (
    categoryRank(a.asset.category) - categoryRank(b.asset.category) ||
    a.asset.type.localeCompare(b.asset.type) ||
    a.asset.name.localeCompare(b.asset.name)
  );
}

/** True when following parentId upwards from `parentId` comes back to `id`. */
function closesCycle(id: string, parentId: string, byId: Map<string, UnifiedAsset>): boolean {
  let current: string | undefined = parentId;
  for (let steps = 0; current && steps <= byId.size; steps++) {
    if (current === id) return true;
    current = byId.get(current)?.parentId;
  }
  return false;
}

/**
 * Nest assets under the parent their parentId names. An asset whose parent is
 * not loaded, or whose parent chain loops back to it, stays at root.
 */
export function buildAssetTree(assets: UnifiedAsset[]): AssetTreeNode[] {
  const byId = new Map(assets.map(a => [a.id, a]));
  const nodes = new Map<string, AssetTreeNode>(
    assets.map(a => [a.id, { asset: a, children: [], childCount: 0 }]),
  );
  const roots: AssetTreeNode[] = [];

  for (const node of nodes.values()) {
    const { id, parentId } = node.asset;
    const parent = parentId ? nodes.get(parentId) : undefined;
    if (parent && !closesCycle(id, parentId!, byId)) {
      parent.children.push(node);
      parent.childCount++;
    } else {
      roots.push(node);
    }
  }

  const sortChildren = (list: AssetTreeNode[]): void => {
    for (const node of list) {
      node.children.sort(compareChildren);
      sortChildren(node.children);
    }
  };
  roots.sort(compareRoots);
  sortChildren(roots);
  return roots;
}
