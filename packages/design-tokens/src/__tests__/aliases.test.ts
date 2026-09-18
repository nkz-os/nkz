import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const preset = () => readFileSync(join(__dirname, '../../dist/tailwind-preset.js'), 'utf8');

describe('alias de la familia bg (D4)', () => {
  it("'bg' mapea a canvas", () => {
    expect(preset()).toMatch(/'bg':\s*'var\(--nkz-color-canvas\)'/);
  });
  it("'bg-secondary' mapea a surface-sunken", () => {
    expect(preset()).toMatch(/'bg-secondary':\s*'rgb\(var\(--nkz-color-surface-sunken-rgb\) \/ <alpha-value>\)'/);
  });
  it("'bg-muted' mapea a surface-sunken", () => {
    expect(preset()).toMatch(/'bg-muted':\s*'rgb\(var\(--nkz-color-surface-sunken-rgb\) \/ <alpha-value>\)'/);
  });
});
