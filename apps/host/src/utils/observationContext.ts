/**
 * Client-side mirror of the observation-context rules (services/common/observation_context.py).
 * Used for hints and to block an obviously invalid form only: the server stays authoritative.
 */

export const MEDIA = [
  'ambientAir',
  'ambientAirUnderStructure',
  'enclosedAir',
  'soil',
  'plant',
  'water',
  'equipment',
] as const;
export type Medium = (typeof MEDIA)[number];

export const ORGANS = ['stem', 'leaf', 'fruit', 'canopy', 'root'] as const;
export type Organ = (typeof ORGANS)[number];

export interface ObservationContext {
  medium: Medium;
  heightAboveGround?: number;
  structure?: string;
  enclosure?: string;
  depthFrom?: number;
  depthTo?: number;
  organ?: Organ;
}

export type ContextField = Exclude<keyof ObservationContext, 'medium'>;

/** medium -> [required fields, optional fields] */
export const MEDIUM_FIELDS: Record<Medium, { required: ContextField[]; optional: ContextField[] }> = {
  ambientAir: { required: [], optional: ['heightAboveGround'] },
  ambientAirUnderStructure: { required: ['structure'], optional: ['heightAboveGround'] },
  enclosedAir: { required: ['enclosure'], optional: ['heightAboveGround'] },
  soil: { required: ['depthFrom', 'depthTo'], optional: [] },
  plant: { required: ['organ'], optional: [] },
  water: { required: [], optional: [] },
  equipment: { required: [], optional: [] },
};

const URN_PREFIX = 'urn:ngsi-ld:';

export function isMedium(value: unknown): value is Medium {
  return typeof value === 'string' && (MEDIA as readonly string[]).includes(value);
}

/** The context with only the fields its medium allows, so a medium change never leaks old fields. */
export function contextForMedium(medium: Medium, previous?: ObservationContext): ObservationContext {
  const { required, optional } = MEDIUM_FIELDS[medium];
  const next: ObservationContext = { medium };
  if (previous) {
    for (const field of [...required, ...optional]) {
      if (previous[field] !== undefined) (next as unknown as Record<string, unknown>)[field] = previous[field];
    }
  }
  return next;
}

/**
 * Stable error codes of a context (empty = valid); the UI translates them as
 * `wizard.observation.error.<code>`.
 */
export function contextErrors(ctx: ObservationContext | null | undefined): string[] {
  if (!ctx || !isMedium(ctx.medium)) return ['medium_invalid'];
  const errors: string[] = [];
  const { required, optional } = MEDIUM_FIELDS[ctx.medium];
  const allowed = new Set<string>([...required, ...optional]);
  for (const key of Object.keys(ctx)) {
    if (key !== 'medium' && ctx[key as ContextField] !== undefined && !allowed.has(key)) {
      errors.push(`${key}_not_allowed`);
    }
  }
  const isNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
  for (const field of required) {
    if (ctx[field] === undefined || ctx[field] === '') errors.push(`${field}_required`);
  }
  for (const field of ['heightAboveGround', 'depthFrom', 'depthTo'] as const) {
    const value = ctx[field];
    if (value !== undefined && !isNumber(value)) errors.push(`${field}_number`);
  }
  if (isNumber(ctx.heightAboveGround) && ctx.heightAboveGround < 0) errors.push('heightAboveGround_negative');
  if (ctx.medium === 'soil' && isNumber(ctx.depthFrom) && isNumber(ctx.depthTo)) {
    if (!(ctx.depthFrom >= 0 && ctx.depthFrom < ctx.depthTo)) errors.push('depth_range');
  }
  for (const field of ['structure', 'enclosure'] as const) {
    const value = ctx[field];
    if (value !== undefined && value !== '' && !(typeof value === 'string' && value.startsWith(URN_PREFIX))) {
      errors.push(`${field}_urn`);
    }
  }
  if (ctx.organ !== undefined && !(ORGANS as readonly string[]).includes(ctx.organ)) errors.push('organ_invalid');
  return errors;
}

/** Entity types an `observesFeature` may point to in the wizard. */
export const FEATURE_TYPES = [
  'AgriParcel',
  'AgriManagementZone',
  'AgriPlant',
  'AgriGreenhouse',
  'Greenhouse',
  'AgriSolarPark',
] as const;
