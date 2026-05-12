import { describe, it, expect } from 'vitest';
import { defineModule } from '../src/defineModule';

describe('defineModule', () => {
  const minimal = {
    id: 'test-module',
    displayName: 'Test',
    hostApiVersion: '^2.0.0',
    accent: { base: '#A16207', soft: '#FEF3C7', strong: '#713F12' },
  };

  it('returns the options unchanged for a valid module', () => {
    const result = defineModule(minimal);
    expect(result.id).toBe('test-module');
    expect(result.displayName).toBe('Test');
  });

  it('throws with a clear message for invalid id', () => {
    expect(() => defineModule({ ...minimal, id: 'BadCase' })).toThrowError(/id.*kebab-case/i);
  });

  it('throws when accent is missing', () => {
    const { accent, ...withoutAccent } = minimal;
    expect(() => defineModule(withoutAccent as never)).toThrowError(/accent/i);
  });

  it('throws when displayName is empty', () => {
    expect(() => defineModule({ ...minimal, displayName: '' })).toThrowError(/displayName/);
  });

  it('preserves all optional fields when valid', () => {
    const full = {
      ...minimal,
      version: '1.0.0',
      route: '/test',
      api: { basePath: '/api/test' },
      requiredPlan: 'basic' as const,
      data: { entities: ['Test'] },
    };
    const result = defineModule(full);
    expect(result.version).toBe('1.0.0');
    expect(result.api?.basePath).toBe('/api/test');
    expect(result.requiredPlan).toBe('basic');
  });
});
