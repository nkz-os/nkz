import { describe, it, expect } from 'vitest';
import { defineModule, toNKZRegistration } from '../src/defineModule';

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

describe('toNKZRegistration', () => {
  const baseModule = {
    id: 'test-mod',
    displayName: 'Test',
    hostApiVersion: '^2.0.0',
    accent: { base: '#A16207', soft: '#FEF3C7', strong: '#713F12' },
  };

  it('maps id and version through verbatim', () => {
    const reg = toNKZRegistration({ ...baseModule, version: '1.2.3' });
    expect(reg.id).toBe('test-mod');
    expect(reg.version).toBe('1.2.3');
  });

  it('leaves version undefined when the module omits it', () => {
    const reg = toNKZRegistration(baseModule);
    expect(reg.version).toBeUndefined();
  });

  it('produces SlotWidgetDefinition entries with localComponent and string component name', () => {
    const FakeWidget = () => null;
    Object.defineProperty(FakeWidget, 'displayName', { value: 'FakeWidget' });

    const reg = toNKZRegistration({
      ...baseModule,
      slots: {
        'context-panel': [
          { id: 'my-widget', component: FakeWidget, priority: 10 },
        ],
      },
    });

    const panel = reg.viewerSlots?.['context-panel' as never] as Array<{ id: string; moduleId?: string; component: string; priority: number; localComponent?: unknown }> | undefined;
    expect(panel).toHaveLength(1);
    expect(panel?.[0].id).toBe('my-widget');
    expect(panel?.[0].moduleId).toBe('test-mod');
    expect(panel?.[0].component).toBe('FakeWidget');
    expect(panel?.[0].priority).toBe(10);
    expect(panel?.[0].localComponent).toBe(FakeWidget);
  });

  it('defaults priority to 50 when omitted', () => {
    const FakeWidget = () => null;
    const reg = toNKZRegistration({
      ...baseModule,
      slots: {
        'context-panel': [{ id: 'my-widget', component: FakeWidget }],
      },
    });
    const panel = reg.viewerSlots?.['context-panel' as never] as Array<{ priority: number }> | undefined;
    expect(panel?.[0].priority).toBe(50);
  });

  it('falls back component name to the entry id when the React component has no name', () => {
    const Anon: unknown = (() => null) as unknown;
    Object.defineProperty(Anon, 'name', { value: '' });

    const reg = toNKZRegistration({
      ...baseModule,
      slots: {
        'context-panel': [{ id: 'fallback-id', component: Anon as never }],
      },
    });
    const panel = reg.viewerSlots?.['context-panel' as never] as Array<{ component: string }> | undefined;
    expect(panel?.[0].component).toBe('fallback-id');
  });
});
