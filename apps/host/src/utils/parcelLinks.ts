/**
 * Links of a Device: `controlledAsset`, always stored as a list. The first URN
 * is the owner (a solar park or a parcel); the parcels stay in the list.
 */
import { relationshipTargets } from './entityRelations';

/* eslint-disable @typescript-eslint/no-explicit-any */
const NGSI_LD_NULL = 'urn:ngsi-ld:null';

const PARCEL_PREFIX = 'urn:ngsi-ld:AgriParcel:';
const PARK_PREFIX = 'urn:ngsi-ld:AgriSolarPark:';

/** Every `controlledAsset` URN, in stored order; accepts string, `{object}` and lists. */
export function readControlledLinks(entity: unknown): string[] {
  if (!entity || typeof entity !== 'object') return [];
  const value = (entity as Record<string, unknown>).controlledAsset;
  return relationshipTargets(value).filter((urn) => urn && urn !== NGSI_LD_NULL);
}

/** Parcel URNs only (a park owner is not included), in stored order. */
export function readParcelLinks(entity: unknown): string[] {
  return readControlledLinks(entity).filter((urn) => urn.startsWith(PARCEL_PREFIX));
}

/** Owner = first entry of the list; the kind comes from the URN prefix. */
export function readLinkOwner(entity: unknown): { urn: string; kind: 'park' | 'parcel' } | null {
  const [first] = readControlledLinks(entity);
  if (!first) return null;
  if (first.startsWith(PARK_PREFIX)) return { urn: first, kind: 'park' };
  if (first.startsWith(PARCEL_PREFIX)) return { urn: first, kind: 'parcel' };
  return null;
}

const isManaged = (urn: string) => urn.startsWith(PARCEL_PREFIX) || urn.startsWith(PARK_PREFIX);

/** `controlledAsset` URNs that are neither a park nor a parcel (set by other tools); never edited here. */
export function readForeignLinks(entity: unknown): string[] {
  return readControlledLinks(entity).filter((urn) => !isManaged(urn));
}

/**
 * `[park?, ...parcels, ...preserved]`, deduped; a sensor always keeps at least one parcel.
 * `preserved` carries the foreign URNs so a save never drops them; park/parcel URNs in it are ignored.
 */
export function deviceLinks(
  ownerParkUrn: string | null,
  parcelUrns: string[],
  preserved: string[] = [],
): string[] {
  if (parcelUrns.length === 0) throw new Error('A sensor requires at least one parcel');
  return Array.from(
    new Set([
      ...(ownerParkUrn ? [ownerParkUrn] : []),
      ...parcelUrns,
      ...preserved.filter((urn) => !isManaged(urn)),
    ]),
  );
}

export interface ParkOption {
  id: string;
  name: string;
  parcelUrns: string[];
}

/** Park entity (raw broker JSON) to a selectable option. */
export function toParkOption(entity: Record<string, any>): ParkOption {
  const rawName = entity.name?.value ?? entity.name;
  return {
    id: String(entity.id),
    name: typeof rawName === 'string' && rawName ? rawName : String(entity.id),
    parcelUrns: relationshipTargets(entity.hasAgriParcel ?? entity.refAgriParcel),
  };
}

/** Parks standing on at least one of the selected parcels. */
export function parkOptionsFor(parks: ParkOption[], selectedParcelUrns: string[]): ParkOption[] {
  return parks.filter((park) => park.parcelUrns.some((urn) => selectedParcelUrns.includes(urn)));
}

/** Merge-patch fragment for `controlledAsset`: deduped, first-appearance order, never empty. */
export function parcelLinksFragment(urns: string[]): {
  controlledAsset: { type: 'Relationship'; object: string[] };
} {
  const object = Array.from(new Set(urns.filter(Boolean)));
  if (object.length === 0) {
    throw new Error('controlledAsset requires at least one parcel');
  }
  return { controlledAsset: { type: 'Relationship', object } };
}
