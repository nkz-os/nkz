import { readFileSync, existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { z } from 'zod';
import chalk from 'chalk';
import manifestSchema from '../schema/manifest-schema.json' with { type: 'json' };
import { KNOWN_ENTITY_TYPES } from '../rules/ngsi-entities.js';
import { validateI18n } from '../rules/i18n.js';
import type { ValidationError } from '../types.js';

export async function validateCommand(
  modulePath: string,
  options: { strict: boolean },
): Promise<void> {
  const absPath = resolve(modulePath);
  const errors: ValidationError[] = [];

  console.log(chalk.blue(`Validating module at ${absPath}\n`));

  // 1. manifest.json exists
  const manifestPath = join(absPath, 'manifest.json');
  if (!existsSync(manifestPath)) {
    errors.push({ file: 'manifest.json', message: 'manifest.json not found', severity: 'error' });
    printErrors(errors, options);
    process.exit(1);
  }

  // 2. manifest.json is valid JSON
  let manifest: unknown;
  try {
    manifest = JSON.parse(readFileSync(manifestPath, 'utf-8'));
  } catch (e) {
    errors.push({
      file: 'manifest.json',
      message: `Invalid JSON: ${(e as Error).message}`,
      severity: 'error',
    });
    printErrors(errors, options);
    process.exit(1);
  }

  // 3. manifest.json schema validation
  const manifestValidator = z.object({
    id: z.string().min(2).regex(/^[a-z][a-z0-9-]*$/,
      'module ID must be lowercase alphanumeric with hyphens'),
    name: z.string().min(1),
    display_name: z.string().min(1),
    version: z.string().regex(/^\d+\.\d+\.\d+$/,
      'version must be semver (x.y.z)'),
    description: z.string().min(1),
    api_contract: z.object({
      hostApiVersion: z.string().min(1),
      moduleApiVersion: z.string().min(1),
      sdkVersion: z.string().min(1),
    }).optional(),
    build_config: z.object({
      type: z.literal('iife'),
      remote_entry_url: z.string().min(1),
    }),
  }).passthrough();

  const result = manifestValidator.safeParse(manifest);
  if (!result.success) {
    for (const issue of result.error.issues) {
      errors.push({
        file: 'manifest.json',
        message: `${issue.path.join('.')}: ${issue.message}`,
        severity: 'error',
      });
    }
  }

  // 4. api_contract should be present (warning if missing, error in strict mode)
  const m = manifest as Record<string, unknown>;
  if (!m.api_contract) {
    errors.push({
      file: 'manifest.json',
      message: 'Missing api_contract — should declare hostApiVersion, moduleApiVersion, sdkVersion',
      severity: options.strict ? 'error' : 'warning',
    });
  }

  // 5. i18n: es.json and en.json present with matching keys
  const i18nErrors = validateI18n(absPath);
  errors.push(...i18nErrors);

  // 6. NGSI-LD: check for unknown entity types
  errors.push(...validateNGSIEntityTypes(manifest));

  // 7. Build output check
  if (m.build_config && typeof m.build_config === 'object') {
    const bc = m.build_config as Record<string, unknown>;
    if (bc.remote_entry_url && typeof bc.remote_entry_url === 'string') {
      const filename = bc.remote_entry_url.split('/').pop() || 'nkz-module.js';
      const localDist = join(absPath, 'dist', filename);
      if (!existsSync(localDist)) {
        errors.push({
          file: 'manifest.json',
          message: `Build output not found at dist/${filename} — run build first`,
          severity: 'warning',
        });
      }
    }
  }

  printErrors(errors, options);

  const hasErrors = errors.some((e) => e.severity === 'error');
  if (hasErrors) {
    console.log(chalk.red('❌ Validation failed\n'));
    process.exit(1);
  }

  console.log(chalk.green('✅ Module validation passed\n'));
}

function validateNGSIEntityTypes(manifest: unknown): ValidationError[] {
  const errors: ValidationError[] = [];
  const m = manifest as Record<string, unknown>;

  if (m.slots && typeof m.slots === 'object') {
    for (const [, widgets] of Object.entries(m.slots)) {
      if (!Array.isArray(widgets)) continue;
      for (const widget of widgets) {
        if (widget && typeof widget === 'object') {
          const w = widget as Record<string, unknown>;
          if (w.showWhen && typeof w.showWhen === 'object') {
            const sw = w.showWhen as Record<string, unknown>;
            if (Array.isArray(sw.entityType)) {
              for (const et of sw.entityType) {
                if (typeof et === 'string' && !KNOWN_ENTITY_TYPES.has(et)) {
                  errors.push({
                    file: 'manifest.json',
                    message: `Unknown entity type "${et}" in slot "${(w.id as string) || 'unknown'}" — not in FIWARE Smart Data Models. Verify before deploying.`,
                    severity: 'warning',
                  });
                }
              }
            }
          }
        }
      }
    }
  }

  return errors;
}

function printErrors(errors: ValidationError[], options: { strict: boolean }): void {
  if (errors.length === 0) return;

  const errorCount = errors.filter((e) => e.severity === 'error').length;
  const warnCount = errors.filter((e) => e.severity === 'warning').length;

  for (const err of errors) {
    const icon = err.severity === 'error' ? '❌' : '⚠️';
    const colorFn = err.severity === 'error' ? chalk.red : chalk.yellow;
    console.log(colorFn(`${icon} ${err.file}: ${err.message}`));
  }

  console.log('');
  const summary: string[] = [];
  if (errorCount > 0) summary.push(chalk.red(`${errorCount} error(s)`));
  if (warnCount > 0) summary.push(chalk.yellow(`${warnCount} warning(s)`));
  console.log(`Found ${summary.join(', ')}`);
}
