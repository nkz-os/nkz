/**
 * Every nkz colour class used in the host must resolve to a real token.
 *
 * Tailwind only emits a utility when the class appears in `content` AND the
 * token exists in the theme. A class that fails the second half is silently
 * dropped: no error, no warning, and the element renders with the inherited
 * value. `text-nkz-muted` was called 531 times here and never existed.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// The preset is a CommonJS file inside a "type": "module" package, so neither
// import nor require reaches it from vitest. Evaluating its text keeps the test
// reading the same artefact Tailwind consumes, instead of a second copy of the
// token list that could drift from it.
const PRESET_PATH = resolve(
  __dirname,
  '../../../../../packages/design-tokens/dist/tailwind-preset.js',
);
type ColourNode = string | { [key: string]: ColourNode };
interface TailwindPreset {
  theme?: {
    extend?: { colors?: { nkz?: ColourNode } };
    colors?: { nkz?: ColourNode };
  };
}

function loadPreset(): TailwindPreset {
  const src = readFileSync(PRESET_PATH, 'utf8');
  const mod: { exports: TailwindPreset } = { exports: {} };
  new Function('module', 'exports', src)(mod, mod.exports);
  return mod.exports;
}
const preset = loadPreset();

const ROOTS = [
  resolve(__dirname, '../../'),                              // apps/host/src
  resolve(__dirname, '../../../../../packages/ui-kit/src'),
  resolve(__dirname, '../../../../../packages/viewer-kit/src'),
];

const COLOUR_PREFIX =
  '(?:text|bg|border|ring|fill|stroke|divide|outline|placeholder|caret|accent|decoration|from|to|via)';
const CLASS_RE = new RegExp(`\\b${COLOUR_PREFIX}-nkz-([a-z0-9-]+)`, 'g');

/** Shared with spacing, radius and font-size scales — not colours. */
const SCALE_TOKENS = /^(2xs|xs|sm|base|md|lg|xl|2xl|3xl|full|tight|inline|stack|section)$/;

/**
 * Colour names with no token behind them, left as-is on purpose.
 *
 * The `bg-*` family has no `--nkz-color-bg-*` variable at all, so there is no
 * intended value to restore: picking a surface for 311 call sites is a design
 * decision, not a rename. Listed here so the test still fails on anything new.
 */
const KNOWN_BROKEN = new Set(['bg-secondary', 'bg', 'bg-muted']);

function walk(dir: string): string[] {
  let out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== 'node_modules' && name !== '__tests__') out = out.concat(walk(p));
    } else if (/\.(tsx?|jsx?)$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}

/** Flatten the preset colour tree into the token names Tailwind will accept. */
function tokenNames(): Set<string> {
  const colors: ColourNode = preset?.theme?.extend?.colors?.nkz ?? preset?.theme?.colors?.nkz ?? {};
  const names = new Set<string>();
  const visit = (node: ColourNode, prefix: string) => {
    if (typeof node === 'string') {
      if (prefix) names.add(prefix);
      return;
    }
    if (node && typeof node === 'object') {
      for (const [k, v] of Object.entries(node) as [string, ColourNode][]) {
        const next = k === 'DEFAULT' ? prefix : prefix ? `${prefix}-${k}` : k;
        visit(v, next);
      }
    }
  };
  visit(colors, '');
  return names;
}

describe('nkz colour tokens', () => {
  it('resolves every colour class the host and kits use', () => {
    const known = tokenNames();
    expect(known.size, 'preset exposes no nkz colours — import path drifted').toBeGreaterThan(10);

    const offenders = new Map<string, string[]>();
    for (const root of ROOTS) {
      for (const file of walk(root)) {
        const src = readFileSync(file, 'utf8');
        for (const [, token] of src.matchAll(CLASS_RE)) {
          // Spacing/radius/z-index share the nkz- prefix; only colours are checked here.
          if (known.has(token)) continue;
          if (SCALE_TOKENS.test(token)) continue;
          if (KNOWN_BROKEN.has(token)) continue;
          const list = offenders.get(token) ?? [];
          list.push(file.split('/').slice(-1)[0]);
          offenders.set(token, list);
        }
      }
    }

    const summary = [...offenders.entries()]
      .sort((a, b) => b[1].length - a[1].length)
      .map(([token, files]) => `nkz-${token} (${files.length}x, e.g. ${files[0]})`);
    expect(summary, 'colour classes with no matching token — they render unstyled').toEqual([]);
  });
});
