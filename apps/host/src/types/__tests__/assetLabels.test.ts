/**
 * Asset type and category labels come from the locales, not from the registry, so the
 * asset panel follows the user's language. The locale parity test then requires every
 * label in all six languages.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  ASSET_TYPE_REGISTRY,
  CATEGORY_REGISTRY,
  assetTypeLabel,
  assetTypeLabelKey,
  categoryLabel,
  categoryLabelKey,
} from '../assets';

const es = JSON.parse(
  readFileSync(resolve(__dirname, '../../../public/locales/es/common.json'), 'utf8'),
) as Record<string, unknown>;

function lookup(key: string): unknown {
  return key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], es);
}

describe('asset labels', () => {
  it.each(Object.keys(ASSET_TYPE_REGISTRY))('has a label for the %s type', type => {
    expect(typeof lookup(assetTypeLabelKey(type))).toBe('string');
  });

  it.each(Object.keys(CATEGORY_REGISTRY))('has a label for the %s category', category => {
    expect(typeof lookup(categoryLabelKey(category as keyof typeof CATEGORY_REGISTRY))).toBe('string');
  });

  it('keeps no hardcoded label in the registries', () => {
    for (const info of [...Object.values(ASSET_TYPE_REGISTRY), ...Object.values(CATEGORY_REGISTRY)]) {
      expect(info).not.toHaveProperty('label');
    }
  });

  it('translates through the given function', () => {
    const t = (key: string) => (key === 'entities.assets.types.AgriCrop' ? 'Crop' : key);
    expect(assetTypeLabel('AgriCrop', t)).toBe('Crop');
    expect(categoryLabel('parcels', key => (key === 'entities.categories.parcels' ? 'Parcels' : key))).toBe('Parcels');
  });

  it('shows the NGSI-LD type of a type without a label', () => {
    expect(assetTypeLabel('SomethingNew', key => key)).toBe('SomethingNew');
  });
});
