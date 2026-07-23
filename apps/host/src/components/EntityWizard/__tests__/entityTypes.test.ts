import { describe, it, expect } from 'vitest';
import { ENTITY_TYPE_METADATA, ENTITY_CATEGORIES } from '../entityTypes';

describe('ENTITY_TYPE_METADATA', () => {
  it('does not offer AgriCrop as a creatable entity type', () => {
    expect(Object.keys(ENTITY_TYPE_METADATA)).not.toContain('AgriCrop');
  });

  it('still offers the other Cultivos-category types', () => {
    const keys = Object.keys(ENTITY_TYPE_METADATA);
    expect(keys).toContain('AgriParcel');
    expect(keys).toContain('Vineyard');
    expect(keys).toContain('OliveGrove');
  });
});

describe('ENTITY_CATEGORIES', () => {
  it('does not list AgriCrop under any category', () => {
    const allTypes = Object.values(ENTITY_CATEGORIES).flat();
    expect(allTypes).not.toContain('AgriCrop');
  });

  it('keeps the Cultivos category with its other types', () => {
    expect(ENTITY_CATEGORIES['Cultivos']).toEqual(['Vineyard', 'OliveGrove', 'AgriParcel']);
  });
});
