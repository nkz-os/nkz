// build-css.ts — generates tokens.css from tokens.config.ts
// Output: dist/tokens.css with CSS custom properties for each web profile.
// Profiles: page, viewer, viewer-light, field (hmi is JS-only, no CSS emitted).

import { writeFileSync, mkdirSync } from 'fs';
import { profiles, cssProfiles, type TokenProfileDefinition, type TokenProfile } from './tokens.config';

function toVarName(key: string): string {
  // camelCase → kebab-case with --nkz- prefix
  return `--nkz-${key.replace(/([A-Z])/g, '-$1').toLowerCase()}`;
}

function buildProfileCSS(name: TokenProfile, def: TokenProfileDefinition): string {
  const lines: string[] = [];
  const selector = name === 'page' ? ':root, [data-theme="page"]' : `[data-theme="${name}"]`;

  lines.push(`/* ${name} profile */`);
  lines.push(`${selector} {`);

  // Colors
  for (const [key, value] of Object.entries(def.colors)) {
    if (typeof value === 'string') {
      lines.push(`  ${toVarName(`color-${key}`)}: ${value};`);
    }
  }

  // Type scale
  for (const [size, spec] of Object.entries(def.type)) {
    lines.push(`  ${toVarName(`type-${size}-size`)}: ${spec.size};`);
    lines.push(`  ${toVarName(`type-${size}-line-height`)}: ${spec.lineHeight};`);
    lines.push(`  ${toVarName(`type-${size}-letter-spacing`)}: ${spec.letterSpacing};`);
    lines.push(`  ${toVarName(`type-${size}-weight`)}: ${spec.fontWeight};`);
  }

  // Radii
  for (const [key, value] of Object.entries(def.radii)) {
    lines.push(`  ${toVarName(`radius-${key}`)}: ${value};`);
  }

  // Shadows
  for (const [key, value] of Object.entries(def.shadows)) {
    if (typeof value === 'string') {
      lines.push(`  ${toVarName(`shadow-${key}`)}: ${value};`);
    }
  }

  // Motion
  for (const [key, value] of Object.entries(def.motion)) {
    lines.push(`  ${toVarName(`motion-${key}`)}: ${value};`);
  }

  // Z-index
  for (const [key, value] of Object.entries(def.zIndex)) {
    lines.push(`  ${toVarName(`z-${key}`)}: ${value};`);
  }

  // Space
  for (const [key, value] of Object.entries(def.space)) {
    lines.push(`  ${toVarName(`space-${key}`)}: ${value};`);
  }

  // Glass effect (multi-line CSS, not a single value)
  if (def.glass && def.glass !== 'none') {
    lines.push(`  /* glass effect */`);
    // Split glass into individual declarations (it may contain ; separated rules)
    for (const rule of def.glass.split(';').map(s => s.trim()).filter(Boolean)) {
      lines.push(`  ${rule};`);
    }
  }

  // Header layout variables (always emitted, shared)
  lines.push(`  --nkz-host-header-h: 56px;`);
  lines.push(`  --nkz-page-header-h: 96px;`);

  lines.push('}');
  lines.push('');
  return lines.join('\n');
}

function buildCSS(): string {
  const parts: string[] = [
    '/* Nekazari Design Tokens — AUTO-GENERATED. Do not edit directly. */',
    '/* Source: packages/design-tokens/src/tokens.config.ts */',
    '',
    '/* Font faces */',
    "@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');",
    '',
    ':root {',
    '  font-family: "Inter", system-ui, -apple-system, sans-serif;',
    '}',
    '',
    'code, pre, .mono {',
    '  font-family: "JetBrains Mono", monospace;',
    '}',
    '',
    '/* Reduced motion — honored by all profiles */',
    '@media (prefers-reduced-motion: reduce) {',
    '  :root {',
    '    --nkz-motion-fast: 0ms;',
    '    --nkz-motion-normal: 0ms;',
    '    --nkz-motion-slow: 0ms;',
    '  }',
    '}',
    '',
  ];

  for (const profile of cssProfiles) {
    parts.push(buildProfileCSS(profile, profiles[profile]));
  }

  return parts.join('\n');
}

const css = buildCSS();
mkdirSync('dist', { recursive: true });
writeFileSync('dist/tokens.css', css);
console.log(`Generated dist/tokens.css (${(css.length / 1024).toFixed(1)} KB)`);
