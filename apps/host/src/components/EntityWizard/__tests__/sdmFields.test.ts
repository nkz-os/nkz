import { describe, it, expect } from 'vitest';
import { missingSdmFields, sdmAttributes, optionKey } from '../sdmFields';

describe('missingSdmFields', () => {
  it('lists the SDM-required fields still empty', () => {
    expect(missingSdmFields('Animal', { species: 'sheep', legalId: ' ' })).toEqual(['legalId', 'sex']);
    expect(missingSdmFields('Animal', { species: 'sheep', legalId: 'ES0123', sex: 'female' })).toEqual([]);
  });

  it('asks nothing of types without SDM fields', () => {
    expect(missingSdmFields('Tank', {})).toEqual([]);
    expect(missingSdmFields('AgriGreenhouse', {})).toEqual([]);
  });
});

describe('sdmAttributes', () => {
  it('writes Building category as a list and the address as an object', () => {
    const a = sdmAttributes('Building', { category: 'barn', addressLocality: ' Town ', addressRegion: '' });
    expect(a.category).toEqual({ type: 'Property', value: ['barn'] });
    expect(a.address).toEqual({ type: 'Property', value: { addressLocality: 'Town' } });
    expect(a.addressLocality).toBeUndefined();
  });

  it('stamps a battery with its report date', () => {
    const now = new Date('2026-11-09T10:00:00Z');
    const a = sdmAttributes('StorageBatteryDevice', { batteryType: 'Li-Ion', rechargeEnergySource: 'electric' }, now);
    expect(a.batteryType).toEqual({ type: 'Property', value: 'Li-Ion' });
    expect(a.dateLastReported).toEqual({
      type: 'Property', value: { '@type': 'DateTime', '@value': '2026-11-09T10:00:00.000Z' },
    });
  });

  it('keeps other attributes as plain properties', () => {
    expect(sdmAttributes('AgriGreenhouse', { area: 120, coverType: '' })).toEqual({
      area: { type: 'Property', value: 120 },
    });
  });
});

it('turns SDM values with spaces into i18n keys', () => {
  expect(optionKey('species', 'dairy cattle')).toBe('wizard.sdm.options.species.dairy_cattle');
});
