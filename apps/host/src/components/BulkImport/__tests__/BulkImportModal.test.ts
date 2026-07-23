import { describe, it, expect } from 'vitest';
import { ENTITY_TYPES } from '../BulkImportModal';

describe('BulkImportModal ENTITY_TYPES', () => {
  it('does not offer AgriCrop as a bulk-importable type', () => {
    expect(ENTITY_TYPES.map((t) => t.value)).not.toContain('AgriCrop');
  });

  it('still offers the other point-geometry types', () => {
    const values = ENTITY_TYPES.map((t) => t.value);
    expect(values).toContain('AgriTree');
    expect(values).toContain('OliveTree');
    expect(values).toContain('FruitTree');
    expect(values).toContain('Vine');
    expect(values).toContain('AgriSensor');
    expect(values).toContain('Device');
    expect(values).toContain('WaterSource');
  });
});
