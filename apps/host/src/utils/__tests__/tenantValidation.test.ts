import { describe, it, expect } from 'vitest'
import {
  normalizeTenantId,
  validateTenantId,
  getTenantIdRules,
  TENANT_ID_PATTERN,
  MIN_TENANT_ID_LENGTH,
  MAX_TENANT_ID_LENGTH,
} from '../tenantValidation'

describe('normalizeTenantId', () => {
  it.each([
    ['abregoandres', 'abregoandres'],
    ['AbregoAndres', 'abregoandres'],
    ['Test Tenant 1', 'test-tenant-1'],
    ['test_tenant_1', 'test-tenant-1'],
    ['test-tenant-1', 'test-tenant-1'],
    ['Asociación Allotarra', 'asociacion-allotarra'],
    ['Ipa7-laik', 'ipa7-laik'],
    ['  spaces  ', 'spaces'],
    ['multi   spaces', 'multi-spaces'],
    ['multi---hyphens', 'multi-hyphens'],
    ['multi___underscores', 'multi-underscores'],
    ['Mixed_-_separators', 'mixed-separators'],
    ['@@@special!!', 'special'],
    ['café', 'cafe'],
    ['Niño', 'nino'],
    ['Ç-Bezirk', 'c-bezirk'],
  ])('normalizes %s -> %s', (input, expected) => {
    expect(normalizeTenantId(input)).toBe(expected)
  })

  it('returns empty for empty / whitespace / all-symbols input', () => {
    expect(normalizeTenantId('')).toBe('')
    expect(normalizeTenantId('   ')).toBe('')
    expect(normalizeTenantId('@@@')).toBe('')
    expect(normalizeTenantId('---')).toBe('')
    expect(normalizeTenantId('___')).toBe('')
  })

  it('is idempotent', () => {
    for (const s of ['Test Tenant 1', 'test_tenant_1', 'Asociación', 'ABC', 'a-b-c-1-2']) {
      const once = normalizeTenantId(s)
      const twice = normalizeTenantId(once)
      expect(twice).toBe(once)
    }
  })

  it('does not add a tenant- prefix', () => {
    expect(normalizeTenantId('allotarra').startsWith('tenant-')).toBe(false)
    expect(normalizeTenantId('baratze').startsWith('tenant-')).toBe(false)
  })

  it('output (when non-empty) matches TENANT_ID_PATTERN', () => {
    for (const s of ['Test Tenant', 'Asociación Allotarra', 'My-Org 2', 'café']) {
      const out = normalizeTenantId(s)
      if (out) expect(TENANT_ID_PATTERN.test(out)).toBe(true)
    }
  })
})

describe('validateTenantId', () => {
  it('accepts a canonical id', () => {
    const r = validateTenantId('myfarm')
    expect(r.isValid).toBe(true)
    expect(r.normalized).toBe('myfarm')
    expect(r.errorKey).toBeUndefined()
  })

  it('rejects empty / whitespace', () => {
    expect(validateTenantId('').errorKey).toBe('activation.tenant_name_empty')
    expect(validateTenantId('   ').isValid).toBe(false)
  })

  it('rejects all-symbols (normalizes to empty)', () => {
    const r = validateTenantId('!!!')
    expect(r.isValid).toBe(false)
    expect(r.errorKey).toBe('activation.tenant_name_invalid_chars')
  })

  it('rejects too short', () => {
    const r = validateTenantId('ab')
    expect(r.isValid).toBe(false)
    expect(r.errorKey).toBe('activation.tenant_name_too_short')
    expect(r.errorParams?.min).toBe(MIN_TENANT_ID_LENGTH)
  })

  it('rejects too long', () => {
    const longId = 'a'.repeat(MAX_TENANT_ID_LENGTH + 1)
    const r = validateTenantId(longId)
    expect(r.isValid).toBe(false)
    expect(r.errorKey).toBe('activation.tenant_name_too_long')
    expect(r.errorParams?.max).toBe(MAX_TENANT_ID_LENGTH)
  })

  it('accepts maximum length', () => {
    const maxId = 'a'.repeat(MAX_TENANT_ID_LENGTH)
    expect(validateTenantId(maxId).isValid).toBe(true)
  })

  it('emits the normalize-hint warning when the input differs from canonical', () => {
    // Spaces collapse to hyphens — input differs from canonical even
    // after toLowerCase().trim(), so the hint fires.
    const r = validateTenantId('Test Tenant')
    expect(r.isValid).toBe(true)
    expect(r.warningKey).toBe('activation.tenant_name_normalize_hint')
    expect(r.warningParams?.normalized).toBe('test-tenant')
  })

  it('does not emit the warning when input is already canonical', () => {
    expect(validateTenantId('myfarm').warningKey).toBeUndefined()
    // Plain case-only difference is also "already canonical" once
    // lowercased — the hint exists for character-set transforms.
    expect(validateTenantId('MyFarm').warningKey).toBeUndefined()
  })

  it('hyphen-canonical "test-allotarra" round-trips clean from "Test Allotarra"', () => {
    const r = validateTenantId('Test Allotarra')
    expect(r.isValid).toBe(true)
    expect(r.normalized).toBe('test-allotarra')
  })

  it('regression: does NOT produce underscores', () => {
    const r = validateTenantId('test_tenant_1')
    expect(r.normalized).toBe('test-tenant-1')
    expect(r.normalized?.includes('_')).toBe(false)
  })
})

describe('getTenantIdRules', () => {
  it('reports the canonical rule shape', () => {
    const rules = getTenantIdRules()
    expect(rules.minLength).toBe(MIN_TENANT_ID_LENGTH)
    expect(rules.maxLength).toBe(MAX_TENANT_ID_LENGTH)
    expect(rules.allowedChars).toBeDefined()
    expect(rules.description).toMatch(/guiones/)
  })
})
