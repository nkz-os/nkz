import { describe, it, expect } from 'vitest';
import { ModuleDefinitionSchema } from '../src/schema';

describe('ModuleDefinitionSchema', () => {
  const minimalValid = {
    id: 'soil-health',
    displayName: 'Soil Health',
    hostApiVersion: '^2.0.0',
    accent: { base: '#A16207', soft: '#FEF3C7', strong: '#713F12' },
  };

  it('accepts a minimal valid module', () => {
    const result = ModuleDefinitionSchema.safeParse(minimalValid);
    expect(result.success).toBe(true);
  });

  it('rejects an id that is not kebab-case', () => {
    const result = ModuleDefinitionSchema.safeParse({ ...minimalValid, id: 'SoilHealth' });
    expect(result.success).toBe(false);
  });

  it('rejects an id with underscores', () => {
    const result = ModuleDefinitionSchema.safeParse({ ...minimalValid, id: 'soil_health' });
    expect(result.success).toBe(false);
  });

  it('rejects accent without all three fields', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      accent: { base: '#A16207' },
    });
    expect(result.success).toBe(false);
  });

  it('rejects accent with non-hex colors', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      accent: { base: 'red', soft: '#FEF3C7', strong: '#713F12' },
    });
    expect(result.success).toBe(false);
  });

  it('accepts a full module with all optional fields', () => {
    const full = {
      ...minimalValid,
      version: '1.0.0',
      description: 'desc',
      icon: 'sprout',
      route: '/soil-health',
      navigation: {
        section: 'modules' as const,
        priority: 60,
        label: { es: 'Suelo', en: 'Soil' },
      },
      api: { basePath: '/api/soil-health' },
      requiredRoles: ['Farmer'],
      requiredPlan: 'basic' as const,
      data: { entities: ['AgriParcel'], timeseries: ['soil_observations'] },
    };
    const result = ModuleDefinitionSchema.safeParse(full);
    expect(result.success).toBe(true);
  });

  it('rejects route that does not start with /', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      route: 'soil-health',
    });
    expect(result.success).toBe(false);
  });

  it('rejects requiredPlan with an unknown tier', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      requiredPlan: 'gold',
    });
    expect(result.success).toBe(false);
  });

  it('rejects navigation.section with an unknown value', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      navigation: { section: 'random', priority: 10 },
    });
    expect(result.success).toBe(false);
  });

  it('rejects api.basePath that does not start with /api/', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      api: { basePath: 'soil-health' },
    });
    expect(result.success).toBe(false);
  });

  it('rejects hostApiVersion that is not a semver range', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      hostApiVersion: 'latest',
    });
    expect(result.success).toBe(false);
  });

  it("accepts data.entities containing '*' as wildcard", () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      data: { entities: ['*'] },
    });
    expect(result.success).toBe(true);
  });
});
