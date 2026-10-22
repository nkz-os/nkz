import { describe, it, expect } from 'vitest';
import { moduleLabel, moduleDescription, sanitizeI18nMap } from '../moduleI18n';

const mod = {
  id: 'crop-planning',
  name: 'crop_planning',
  displayName: 'Crop Planning Display',
  label: 'Crop Planning Label',
  description: 'Plain description',
  labelI18n: { es: 'Planificación de cultivos', en: 'Crop planning', eu: 'Laboreen planifikazioa' },
  descriptionI18n: { es: 'Descripción', en: 'Description' },
};

describe('moduleLabel', () => {
  it('uses the translation for the language', () => {
    expect(moduleLabel(mod, 'es')).toBe('Planificación de cultivos');
    expect(moduleLabel(mod, 'eu')).toBe('Laboreen planifikazioa');
  });

  it('falls back from a regional tag to its primary language', () => {
    expect(moduleLabel(mod, 'es-ES')).toBe('Planificación de cultivos');
    expect(moduleLabel(mod, 'en_GB')).toBe('Crop planning');
    expect(moduleLabel(mod, 'EN-us')).toBe('Crop planning');
  });

  it('prefers an exact regional key over the primary language', () => {
    const m = { ...mod, labelI18n: { 'pt-br': 'Brasil', pt: 'Portugal' } };
    expect(moduleLabel(m, 'pt-BR')).toBe('Brasil');
    expect(moduleLabel(m, 'pt-PT')).toBe('Portugal');
  });

  it('falls back to label, displayName, name and id in that order', () => {
    expect(moduleLabel(mod, 'fr')).toBe('Crop Planning Label');
    expect(moduleLabel({ ...mod, label: undefined }, 'fr')).toBe('Crop Planning Display');
    expect(moduleLabel({ ...mod, label: '', displayName: '' }, 'fr')).toBe('crop_planning');
    expect(moduleLabel({ id: 'only-id' }, 'fr')).toBe('only-id');
  });

  it('reads a snake_case marketplace row', () => {
    const row = { id: 'x', name: 'x', display_name: 'Marketplace name', labelI18n: { es: 'Nombre' } };
    expect(moduleLabel(row, 'es')).toBe('Nombre');
    expect(moduleLabel(row, 'fr')).toBe('Marketplace name');
  });

  it('skips blank translations', () => {
    const m = { ...mod, labelI18n: { es: '   ', 'es-es': '' } };
    expect(moduleLabel(m, 'es')).toBe('Crop Planning Label');
  });

  it('falls back when the language is missing or the map is absent', () => {
    expect(moduleLabel(mod, undefined)).toBe('Crop Planning Label');
    expect(moduleLabel(mod, '')).toBe('Crop Planning Label');
    expect(moduleLabel({ ...mod, labelI18n: undefined }, 'es')).toBe('Crop Planning Label');
  });

  it('never throws on a malformed map', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = { ...mod, labelI18n: 'oops' as any };
    expect(moduleLabel(m, 'es')).toBe('Crop Planning Label');
  });

  it('returns an empty string when nothing is known', () => {
    expect(moduleLabel({}, 'es')).toBe('');
  });
});

describe('moduleDescription', () => {
  it('uses the translation for the language and its primary subtag', () => {
    expect(moduleDescription(mod, 'es')).toBe('Descripción');
    expect(moduleDescription(mod, 'en-GB')).toBe('Description');
  });

  it('falls back to the plain description', () => {
    expect(moduleDescription(mod, 'fr')).toBe('Plain description');
    expect(moduleDescription(mod, undefined)).toBe('Plain description');
  });

  it('is undefined when there is nothing to show', () => {
    expect(moduleDescription({ id: 'x' }, 'es')).toBeUndefined();
    expect(moduleDescription({ id: 'x', description: '' }, 'es')).toBeUndefined();
  });
});

describe('sanitizeI18nMap', () => {
  it('keeps a plain object of strings', () => {
    expect(sanitizeI18nMap({ es: 'a', en: 'b' })).toEqual({ es: 'a', en: 'b' });
  });

  it('drops non-string and blank entries', () => {
    expect(sanitizeI18nMap({ es: 'a', en: 3, fr: '', pt: '  ', ca: null })).toEqual({ es: 'a' });
  });

  it('is undefined for anything that is not a usable plain object', () => {
    for (const bad of [undefined, null, 'x', 4, [], ['es'], {}, { es: 1 }]) {
      expect(sanitizeI18nMap(bad)).toBeUndefined();
    }
  });
});
