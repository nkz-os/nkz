import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import type { ValidationError } from '../types.js';

export function validateI18n(modulePath: string): ValidationError[] {
  const errors: ValidationError[] = [];
  const localesDir = resolve(modulePath, 'src', 'locales');

  if (!existsSync(localesDir)) {
    errors.push({
      file: 'src/locales/',
      message: 'No locale directory. Modules must provide at least es.json and en.json.',
      severity: 'warning',
    });
    return errors;
  }

  const esPath = resolve(localesDir, 'es.json');
  const enPath = resolve(localesDir, 'en.json');

  if (!existsSync(esPath)) {
    errors.push({
      file: 'src/locales/es.json',
      message: 'Missing Spanish translations (es.json). Required minimum.',
      severity: 'error',
    });
  }
  if (!existsSync(enPath)) {
    errors.push({
      file: 'src/locales/en.json',
      message: 'Missing English translations (en.json). Required minimum.',
      severity: 'error',
    });
  }

  if (!existsSync(esPath) || !existsSync(enPath)) return errors;

  let esKeys: string[] = [];
  let enKeys: string[] = [];

  try {
    esKeys = getAllKeys(JSON.parse(readFileSync(esPath, 'utf-8')));
  } catch (e) {
    errors.push({
      file: 'src/locales/es.json',
      message: `Invalid JSON: ${(e as Error).message}`,
      severity: 'error',
    });
  }

  try {
    enKeys = getAllKeys(JSON.parse(readFileSync(enPath, 'utf-8')));
  } catch (e) {
    errors.push({
      file: 'src/locales/en.json',
      message: `Invalid JSON: ${(e as Error).message}`,
      severity: 'error',
    });
  }

  if (esKeys.length === 0 || enKeys.length === 0) return errors;

  const esKeySet = new Set(esKeys);
  const enKeySet = new Set(enKeys);

  for (const key of esKeys) {
    if (!enKeySet.has(key)) {
      errors.push({
        file: 'src/locales/',
        message: `Key "${key}" exists in es.json but missing in en.json`,
        severity: 'error',
      });
    }
  }
  for (const key of enKeys) {
    if (!esKeySet.has(key)) {
      errors.push({
        file: 'src/locales/',
        message: `Key "${key}" exists in en.json but missing in es.json`,
        severity: 'error',
      });
    }
  }

  return errors;
}

function getAllKeys(obj: Record<string, unknown>, prefix = ''): string[] {
  const keys: string[] = [];
  for (const [key, value] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    keys.push(fullKey);
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      keys.push(...getAllKeys(value as Record<string, unknown>, fullKey));
    }
  }
  return keys;
}
