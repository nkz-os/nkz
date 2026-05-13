import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, rmSync, writeFileSync, mkdirSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { detectEntryStrategy, generateModuleEntry } from '../src/codegen';

describe('detectEntryStrategy', () => {
  let projectRoot: string;

  beforeEach(() => {
    projectRoot = mkdtempSync(join(tmpdir(), 'nkz-codegen-'));
    mkdirSync(join(projectRoot, 'src'), { recursive: true });
  });

  afterEach(() => {
    rmSync(projectRoot, { recursive: true, force: true });
  });

  it('returns "modern" when src/Module.tsx exists', () => {
    writeFileSync(join(projectRoot, 'src/Module.tsx'), 'export default {}');
    expect(detectEntryStrategy(projectRoot)).toBe('modern');
  });

  it('returns "legacy" when only src/moduleEntry.ts exists', () => {
    writeFileSync(join(projectRoot, 'src/moduleEntry.ts'), '// legacy');
    expect(detectEntryStrategy(projectRoot)).toBe('legacy');
  });

  it('prefers "modern" if both exist (modern wins)', () => {
    writeFileSync(join(projectRoot, 'src/Module.tsx'), 'export default {}');
    writeFileSync(join(projectRoot, 'src/moduleEntry.ts'), '// legacy');
    expect(detectEntryStrategy(projectRoot)).toBe('modern');
  });

  it('throws if neither exists', () => {
    expect(() => detectEntryStrategy(projectRoot)).toThrow(/Module\.tsx.*moduleEntry/);
  });
});

describe('generateModuleEntry', () => {
  let projectRoot: string;

  beforeEach(() => {
    projectRoot = mkdtempSync(join(tmpdir(), 'nkz-codegen-'));
    mkdirSync(join(projectRoot, 'src'), { recursive: true });
    writeFileSync(join(projectRoot, 'src/Module.tsx'), 'export default {}');
  });

  afterEach(() => {
    rmSync(projectRoot, { recursive: true, force: true });
  });

  it('writes moduleEntry.gen.ts into node_modules/.nkz/', () => {
    const outPath = generateModuleEntry(projectRoot);
    expect(outPath).toBe(join(projectRoot, 'node_modules/.nkz/moduleEntry.gen.ts'));
    expect(existsSync(outPath)).toBe(true);
  });

  it('the generated file imports from src/Module', () => {
    const outPath = generateModuleEntry(projectRoot);
    const content = readFileSync(outPath, 'utf-8');
    expect(content).toMatch(/import\s+moduleConfig\s+from\s+['"].*\/src\/Module['"];?/);
  });

  it('the generated file calls window.__NKZ__.register', () => {
    const outPath = generateModuleEntry(projectRoot);
    const content = readFileSync(outPath, 'utf-8');
    expect(content).toMatch(/window\.__NKZ__\??\.register/);
  });

  it('the generated file has a banner comment', () => {
    const outPath = generateModuleEntry(projectRoot);
    const content = readFileSync(outPath, 'utf-8');
    expect(content).toMatch(/GENERATED.*do not edit/i);
  });
});

import { generateManifest } from '../src/codegen';

describe('generateManifest', () => {
  const moduleConfig = {
    id: 'soil-health',
    displayName: 'Soil Health',
    version: '1.0.0',
    hostApiVersion: '^2.0.0',
    description: 'Soil module',
    accent: { base: '#A16207', soft: '#FEF3C7', strong: '#713F12' },
    icon: 'sprout',
    route: '/soil-health',
    navigation: { section: 'modules' as const, priority: 60 },
    api: { basePath: '/api/soil-health' },
    requiredRoles: ['Farmer'],
    requiredPlan: 'basic' as const,
    data: { entities: ['AgriParcel'] },
    main: () => null,
    slots: { 'context-panel': [{ id: 'x', component: () => null }] },
    i18n: { es: () => Promise.resolve({}) },
  };

  it('writes a JSON file with public fields only', () => {
    const json = generateManifest(moduleConfig as never);
    expect(json.id).toBe('soil-health');
    expect(json.displayName).toBe('Soil Health');
    expect(json.version).toBe('1.0.0');
    expect(json.hostApiVersion).toBe('^2.0.0');
    expect(json.accent).toEqual({ base: '#A16207', soft: '#FEF3C7', strong: '#713F12' });
    expect(json.route).toBe('/soil-health');
    expect(json.api).toEqual({ basePath: '/api/soil-health' });
  });

  it('strips runtime-only fields (main, slot components, i18n loaders)', () => {
    const json = generateManifest(moduleConfig as never) as Record<string, unknown>;
    expect(json.main).toBeUndefined();
    expect(json.i18n).toBeUndefined();
    const slots = json.slots as Record<string, Array<{ id: string; component?: unknown }>>;
    expect(slots['context-panel'][0].id).toBe('x');
    expect(slots['context-panel'][0].component).toBeUndefined();
  });

  it('preserves slot metadata (id, priority) but not component fn', () => {
    const cfg = {
      ...moduleConfig,
      slots: {
        'bottom-panel': [
          { id: 'widget-a', component: () => null, priority: 10 },
          { id: 'widget-b', component: () => null, priority: 20 },
        ],
      },
    };
    const json = generateManifest(cfg as never) as Record<string, unknown>;
    const slots = json.slots as Record<string, Array<{ id: string; priority?: number; component?: unknown }>>;
    expect(slots['bottom-panel'][0]).toEqual({ id: 'widget-a', priority: 10 });
    expect(slots['bottom-panel'][1]).toEqual({ id: 'widget-b', priority: 20 });
  });

  it('includes only languages from i18n keys (not the loader functions)', () => {
    const json = generateManifest(moduleConfig as never) as Record<string, unknown>;
    expect(json.i18nLangs).toEqual(['es']);
  });
});
