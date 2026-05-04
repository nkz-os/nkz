import { writeFileSync, mkdirSync } from 'fs';
import { profiles } from './tokens.config';

const nativeProfileNames = ['field', 'hmi'] as const;

function flattenColors(colors: Record<string, unknown>): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(colors)) {
    if (typeof value === 'string') {
      result[key] = value;
    } else if (typeof value === 'object' && value !== null && 'base' in value) {
      const obj = value as Record<string, string>;
      result[key] = obj.base;
      result[`${key}Soft`] = obj.soft;
      result[`${key}Strong`] = obj.strong;
    }
  }
  return result;
}

const output: Record<string, Record<string, unknown>> = {};
for (const name of nativeProfileNames) {
  const p = profiles[name];
  output[name] = {
    colors: flattenColors(p.colors as unknown as Record<string, unknown>),
    type: p.type,
    radii: p.radii,
    shadows: p.shadows,
    motion: p.motion,
    space: p.space,
  };
}

const js = `// @nekazari/design-tokens/native — Native tokens for React Native
// Auto-generated from tokens.config.ts. Consumed by nkz-mobile (Spec 3).
// Profiles: ${nativeProfileNames.join(', ')}

export const nativeTokens = ${JSON.stringify(output, null, 2)};
`;

mkdirSync('dist', { recursive: true });
writeFileSync('dist/native-tokens.js', js);
console.log('Generated dist/native-tokens.js');
