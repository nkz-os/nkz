import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, rmSync, writeFileSync, mkdirSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { emitManifest } from '../src/manifestEmitter';

describe('emitManifest', () => {
  let projectRoot: string;

  beforeEach(() => {
    projectRoot = mkdtempSync(join(tmpdir(), 'nkz-manifest-'));
    mkdirSync(join(projectRoot, 'src'), { recursive: true });
    mkdirSync(join(projectRoot, 'dist'), { recursive: true });
    writeFileSync(
      join(projectRoot, 'src/Module.tsx'),
      `export default {
        id: 'test-mod',
        displayName: 'Test',
        version: '1.0.0',
        hostApiVersion: '^2.0.0',
        accent: { base: '#000000', soft: '#111111', strong: '#222222' },
        route: '/test',
        navigation: { section: 'modules', priority: 10 },
        api: { basePath: '/api/test' },
        requiredRoles: ['Farmer'],
        requiredPlan: 'basic',
        data: { entities: ['AgriParcel'] },
      };
      `,
    );
  });

  afterEach(() => {
    rmSync(projectRoot, { recursive: true, force: true });
  });

  it('writes dist/manifest.json with the module fields', async () => {
    const outPath = await emitManifest(projectRoot);
    expect(outPath).toBe(join(projectRoot, 'dist/manifest.json'));
    expect(existsSync(outPath)).toBe(true);
    const content = JSON.parse(readFileSync(outPath, 'utf-8'));
    expect(content.id).toBe('test-mod');
    expect(content.displayName).toBe('Test');
    expect(content.route).toBe('/test');
    expect(content.api).toEqual({ basePath: '/api/test' });
    expect(content.requiredRoles).toEqual(['Farmer']);
    expect(content.data).toEqual({ entities: ['AgriParcel'] });
  });

  it('honours a custom outDir', async () => {
    const customOut = join(projectRoot, 'custom-out');
    mkdirSync(customOut, { recursive: true });
    const outPath = await emitManifest(projectRoot, { outDir: customOut });
    expect(outPath).toBe(join(customOut, 'manifest.json'));
    expect(existsSync(outPath)).toBe(true);
  });

  it('throws if src/Module.tsx is missing', async () => {
    rmSync(join(projectRoot, 'src/Module.tsx'));
    await expect(emitManifest(projectRoot)).rejects.toThrow(/Module\.tsx/);
  });
});
