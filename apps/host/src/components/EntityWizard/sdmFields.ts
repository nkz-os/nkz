/**
 * Smart Data Models fields the wizard asks for, per canonical type.
 *
 * `required` mirrors the SDM schema's `required` list, so an entity the wizard
 * creates is valid against its schema. Values live in
 * `formData.additionalAttributes` under the field key; `sdmAttributes` turns
 * them into NGSI-LD attributes (some SDM attributes are structured: Building
 * `category` is a list, `address` is an object).
 */

export interface SdmField {
  key: string;
  kind: 'text' | 'select' | 'date';
  required?: boolean;
  /** Allowed values (SDM enum). */
  options?: readonly string[];
  /** Option labels come from i18n unless the value is already a universal code. */
  translateOptions?: boolean;
}

const BUILDING_CATEGORIES = [
  'barn', 'cowshed', 'stable', 'sty', 'shed', 'warehouse',
  'farm', 'farm_auxiliary', 'garage', 'hangar', 'digester', 'water_tower',
] as const;

export const SDM_FIELDS: Record<string, readonly SdmField[]> = {
  Building: [
    { key: 'category', kind: 'select', required: true, options: BUILDING_CATEGORIES, translateOptions: true },
    { key: 'addressLocality', kind: 'text', required: true },
    { key: 'addressRegion', kind: 'text' },
    { key: 'addressCountry', kind: 'text' },
  ],
  Animal: [
    { key: 'species', kind: 'select', required: true, translateOptions: true,
      options: ['beef cattle', 'cow', 'dairy cattle', 'goat', 'horse', 'pig', 'sheep'] },
    { key: 'legalId', kind: 'text', required: true },
    { key: 'sex', kind: 'select', required: true, options: ['female', 'male'], translateOptions: true },
    { key: 'breed', kind: 'text' },
    { key: 'birthdate', kind: 'date' },
  ],
  StorageBatteryDevice: [
    { key: 'batteryType', kind: 'select', required: true,
      options: ['alkaline', 'gel', 'lead', 'lead-AGM', 'Li-Ion', 'Li-Po', 'Li-Po4', 'LMP', 'Li-Air', 'Na-NiCl2(Zebra)', 'Ni-Cd', 'Ni-MH'] },
    { key: 'rechargeEnergySource', kind: 'select', required: true,
      options: ['electric', 'hydraulic', 'windTurbine', 'other'], translateOptions: true },
  ],
};

/** Types whose SDM schema requires `location`. */
export const LOCATION_REQUIRED = new Set(['StorageBatteryDevice', 'Tank']);

/** Types whose parcel link is the SDM relationship `locatedAt` (not the legacy refAgriParcel). */
export const LOCATED_AT_TYPES = new Set(['Animal']);

/** i18n key for an option label: spaces in SDM values become underscores. */
export function optionKey(field: string, value: string): string {
  return `wizard.sdm.options.${field}.${value.replace(/\s+/g, '_')}`;
}

/** Keys of required fields still empty. */
export function missingSdmFields(type: string, values: Record<string, unknown>): string[] {
  return (SDM_FIELDS[type] ?? [])
    .filter(f => f.required && !String(values[f.key] ?? '').trim())
    .map(f => f.key);
}

const ADDRESS_KEYS = ['addressLocality', 'addressRegion', 'addressCountry'];

/**
 * NGSI-LD attributes for the SDM fields of `type`. Fields of other types (or
 * none) pass through unchanged as plain Properties.
 */
export function sdmAttributes(
  type: string,
  values: Record<string, unknown>,
  now: Date = new Date(),
): Record<string, unknown> {
  const attrs: Record<string, unknown> = {};
  const fields = SDM_FIELDS[type];
  const filled = (k: string) => values[k] !== '' && values[k] !== null && values[k] !== undefined;

  for (const [k, v] of Object.entries(values)) {
    if (!filled(k)) continue;
    if (type === 'Building' && ADDRESS_KEYS.includes(k)) continue;
    if (type === 'Building' && k === 'category') {
      attrs.category = { type: 'Property', value: [v] };
      continue;
    }
    attrs[k] = { type: 'Property', value: typeof v === 'string' ? v.trim() : v };
  }

  if (type === 'Building') {
    const address: Record<string, string> = {};
    for (const k of ADDRESS_KEYS) if (filled(k)) address[k] = String(values[k]).trim();
    if (Object.keys(address).length) attrs.address = { type: 'Property', value: address };
  }
  if (type === 'StorageBatteryDevice' && fields) {
    attrs.dateLastReported = { type: 'Property', value: { '@type': 'DateTime', '@value': now.toISOString() } };
  }
  return attrs;
}
