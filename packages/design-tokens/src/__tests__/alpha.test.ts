import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const css = () => readFileSync(join(__dirname, '../../dist/tokens.css'), 'utf8');
const preset = () => readFileSync(join(__dirname, '../../dist/tailwind-preset.js'), 'utf8');

describe('emisión dual de canales', () => {
  it('emite la terna RGB para los tokens convertibles', () => {
    expect(css()).toMatch(/--nkz-color-danger-rgb:\s*239 68 68/);
    expect(css()).toMatch(/--nkz-color-surface-sunken-rgb:/);
  });

  it('conserva la variable de color completa', () => {
    expect(css()).toMatch(/--nkz-color-danger:\s*#EF4444/i);
  });

  it('NO emite terna para los tokens no convertibles', () => {
    expect(css()).not.toMatch(/--nkz-color-canvas-rgb:/);
    expect(css()).not.toMatch(/--nkz-color-border-rgb:/);
    expect(css()).not.toMatch(/--nkz-color-border-strong-rgb:/);
  });

  it('el preset usa la forma con <alpha-value> para los convertibles', () => {
    expect(preset()).toMatch(/rgb\(var\(--nkz-color-danger-rgb\) \/ <alpha-value>\)/);
  });

  it('el preset deja los no convertibles como var() sólido', () => {
    expect(preset()).toMatch(/canvas:\s*'var\(--nkz-color-canvas\)'/);
  });
});
