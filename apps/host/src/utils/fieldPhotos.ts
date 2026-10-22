export interface FieldPhotoRecord {
  id: string;
  /** Relative path /api/field-images/<key>. */
  imageUrl: string;
  lng: number | null;
  lat: number | null;
  /** ISO timestamp, '' when absent. */
  dateObserved: string;
  note: string;
  accuracy: number | null;
  refAgriParcel: string | null;
}

/** Parse normalized NGSI-LD AgriParcelRecord entities into a flat view model. */
export function parseFieldPhotos(raw: unknown): FieldPhotoRecord[] {
  if (!Array.isArray(raw)) return [];
  const out: FieldPhotoRecord[] = [];
  for (const e of raw) {
    const imageUrl: string | undefined = e?.imageUrl?.value;
    if (!imageUrl) continue;
    const coords: unknown = e?.location?.value?.coordinates;
    const hasCoords = Array.isArray(coords) && coords.length >= 2;
    out.push({
      id: e.id,
      imageUrl,
      lng: hasCoords ? Number(coords[0]) : null,
      lat: hasCoords ? Number(coords[1]) : null,
      dateObserved: e?.dateObserved?.value ?? '',
      note: e?.note?.value ?? '',
      accuracy: typeof e?.accuracy?.value === 'number' ? e.accuracy.value : null,
      refAgriParcel: e?.refAgriParcel?.object ?? null,
    });
  }
  return out;
}

/**
 * Filter photos to those whose dateObserved is within `windowDays` of `currentDate`
 * (inclusive, symmetric). `windowDays === null` disables the window (all dated photos).
 * Undated photos are always excluded. Result is sorted ascending by date.
 */
export function photosInWindow(
  photos: FieldPhotoRecord[],
  currentDate: Date,
  windowDays: number | null,
): FieldPhotoRecord[] {
  const center = currentDate.getTime();
  const span = windowDays === null ? Infinity : windowDays * 24 * 60 * 60 * 1000;
  return photos
    .filter(p => {
      if (!p.dateObserved) return false;
      const t = new Date(p.dateObserved).getTime();
      if (Number.isNaN(t)) return false;
      return Math.abs(t - center) <= span;
    })
    .sort((a, b) => a.dateObserved.localeCompare(b.dateObserved));
}
