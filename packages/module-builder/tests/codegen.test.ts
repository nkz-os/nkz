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
