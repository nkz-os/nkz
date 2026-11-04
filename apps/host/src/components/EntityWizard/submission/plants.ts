import api from '@/services/api';
import type { PlacementState } from '@/machines/placementMachine';
import type { GeoAssetFormData } from '../types';

/** One individual plant as the plants endpoint takes it (ids and codes are built server-side). */
export interface PlantItem {
  lat: number;
  lon: number;
  plantCode?: string;
  rowNumber?: number;
  positionInRow?: number;
  variety?: string;
  datePlanted?: string;
  plantStatus?: string;
}

export interface PlantsRequest {
  parcel_id: string;
  plants: PlantItem[];
  /** Prefix of the codes assigned server-side (a second block in the same parcel: "B"). */
  code_prefix?: string;
}

export interface PlantsPreview {
  accepted: Array<{ index: number; id: string; plantCode: string; action: 'create' | 'update';
    rowNumber?: number; positionInRow?: number }>;
  rejected: Array<{ index: number | null; plantCode?: string; reason: string; field?: string; distance_m?: number; other?: string }>;
  warnings: Array<{ index: number; plantCode: string; code: string; other?: string; distance_m?: number }>;
}

export interface PlantsWritten {
  written: Array<{ id: string; plantCode: string; rowNumber?: number; positionInRow?: number }>;
  rejected: PlantsPreview['rejected'];
  warnings: PlantsPreview['warnings'];
}

function text(v: unknown): string | undefined {
  return typeof v === 'string' && v.trim() ? v.trim() : undefined;
}

function positiveInt(v: unknown): number | undefined {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() ? Number(v) : NaN;
  return Number.isInteger(n) && n >= 1 ? n : undefined;
}

/** Every placed point is one plant: single click, stamped points, a grid (row/col) or a line (position). */
export function toPlantItems(placement: PlacementState, form: GeoAssetFormData): PlantItem[] {
  const attrs = form.additionalAttributes ?? {};
  const batch: Partial<PlantItem> = {};
  const variety = text(attrs.variety);
  const datePlanted = text(attrs.datePlanted);
  const plantStatus = text(attrs.plantStatus);
  if (variety) batch.variety = variety;
  if (datePlanted) batch.datePlanted = datePlanted;
  if (plantStatus) batch.plantStatus = plantStatus;

  const multi = ['stamp', 'array', 'line'].includes(placement.mode) && placement.stampedInstances.length > 0;
  if (!multi) {
    const g = form.geometry as { type?: string; coordinates?: number[] } | null;
    if (!g || g.type !== 'Point' || !g.coordinates) return [];
    const [lon, lat] = g.coordinates;
    const code = text(attrs.plantCode);
    return [{ lat, lon, ...(code ? { plantCode: code } : {}), ...batch }];
  }
  const lineRow = placement.mode === 'line' ? positiveInt(attrs.rowNumber) : undefined;
  return placement.stampedInstances.map((p) => {
    const item: PlantItem = { lat: p.lat, lon: p.lng, ...batch };
    if (placement.mode === 'array' && p.row && p.col) {
      item.rowNumber = p.row;
      item.positionInRow = p.col;
    } else if (placement.mode === 'line' && p.col) {
      if (lineRow) item.rowNumber = lineRow;
      item.positionInRow = p.col;
    }
    return item;
  });
}

export function plantsRequest(placement: PlacementState, form: GeoAssetFormData): PlantsRequest {
  const parcel = form.parentEntity;
  if (!parcel || parcel.type !== 'AgriParcel') throw new Error('parcel_required');
  const prefix = text(form.additionalAttributes?.codePrefix);
  return { parcel_id: parcel.id, plants: toPlantItems(placement, form), ...(prefix ? { code_prefix: prefix } : {}) };
}

export function previewPlants(placement: PlacementState, form: GeoAssetFormData): Promise<PlantsPreview> {
  return api.previewPlants(plantsRequest(placement, form));
}

export function submitPlants(placement: PlacementState, form: GeoAssetFormData): Promise<PlantsWritten> {
  return api.createPlants(plantsRequest(placement, form));
}
