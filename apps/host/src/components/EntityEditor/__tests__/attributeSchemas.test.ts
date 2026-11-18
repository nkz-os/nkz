import { describe, expect, it } from 'vitest';
import { getSchemasForType, KNOWN_SCHEMAS } from '../attributeSchemas';

const kinematicKeys = (category?: string) =>
  getSchemasForType('ManufacturingMachine', category).filter((s) => s.section === 'kinematic').map((s) => s.key);

describe('machine geometry schemas', () => {
  it('a tractor shows its own geometry and the working fields of a self-propelled machine', () => {
    const k = kinematicKeys('tractor');
    for (const key of ['minTurningRadius', 'trackWidth', 'wheelbase', 'gpsOffsetX', 'hitchOffsetX', 'frontHitchOffsetX',
      'guidanceDeviceOffsetX', 'guidanceDeviceOffsetY', 'implementWidth', 'implementOffsetX', 'implementOffsetY']) {
      expect(k).toContain(key);
    }
    expect(k).not.toContain('mountPosition');
    expect(k).not.toContain('transportWidth');
    expect(k).not.toContain('hitchType');
  });

  it('an implement shows how it hitches and where it works, not the tractor geometry', () => {
    const k = kinematicKeys('implement');
    for (const key of ['hitchType', 'mountPosition', 'implementWidth', 'transportWidth', 'implementOffsetX',
      'implementOffsetY', 'implementLength']) {
      expect(k).toContain(key);
    }
    for (const key of ['minTurningRadius', 'gpsOffsetX', 'hitchOffsetX', 'frontHitchOffsetX', 'guidanceDeviceOffsetX']) {
      expect(k).not.toContain(key);
    }
  });

  it('a machine without (or with an unknown) category shows every geometry field', () => {
    const all = KNOWN_SCHEMAS.filter((s) => s.section === 'kinematic' && s.entityTypes.includes('ManufacturingMachine'))
      .map((s) => s.key);
    expect(kinematicKeys(undefined)).toEqual(all);
    expect(kinematicKeys('harvester')).toEqual(all);
  });

  it('category is a tractor/implement select and mountPosition a rear/front select', () => {
    const cat = getSchemasForType('ManufacturingMachine').find((s) => s.key === 'category')!;
    expect(cat.type).toBe('select');
    expect(cat.options!.map((o) => o.value)).toEqual(['tractor', 'implement']);
    const mount = KNOWN_SCHEMAS.find((s) => s.key === 'mountPosition')!;
    expect(mount.options!.map((o) => o.value)).toEqual(['rear', 'front']);
  });

  it('every geometry length is in metres and explains its reference', () => {
    const lengths = ['minTurningRadius', 'trackWidth', 'wheelbase', 'gpsOffsetX', 'gpsOffsetY', 'gpsOffsetZ', 'hitchOffsetX',
      'frontHitchOffsetX', 'guidanceDeviceOffsetX', 'guidanceDeviceOffsetY', 'implementWidth', 'transportWidth',
      'implementOffsetX', 'implementOffsetY', 'implementLength'];
    for (const key of lengths) {
      const s = KNOWN_SCHEMAS.find((x) => x.key === key)!;
      expect(s.unitCode, key).toBe('MTR');
      expect(s.helpKey, key).toBe(`editor.help.${key}`);
    }
  });
});
