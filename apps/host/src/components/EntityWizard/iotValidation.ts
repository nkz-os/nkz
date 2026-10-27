import type { Geometry, Point } from 'geojson';
import type { IoTSensorFormData } from './types';
import { DATALOGGER_KINDS } from '@/services/dataloggersApi';
import { EXTERNAL_ID_RE } from '@/utils/deviceIds';

/** i18n key of the first problem in the IoT step, or null. Parcels apply only to `Device`. */
export function validateIoTConfig(
  entityType: string | null,
  data: IoTSensorFormData,
): string | null {
  if (!data.deviceProfileId) return 'wizard.validation.device_profile_required';
  if (entityType === 'Device' && data.parcels.length === 0) {
    return 'wizard.validation.parcel_required';
  }
  return null;
}

/** i18n key of the first problem in the datalogger step, or null. Only `Device` has this step. */
export function validateDataloggerStep(
  entityType: string | null,
  data: IoTSensorFormData,
): string | null {
  if (entityType !== 'Device') return null;
  if (data.dataloggerMode === 'new') {
    if (!data.newDataloggerName?.trim()) return 'wizard.validation.datalogger_name_required';
    if (!data.newDataloggerKind || !DATALOGGER_KINDS.includes(data.newDataloggerKind)) {
      return 'wizard.validation.datalogger_kind_required';
    }
  } else if (!data.dataloggerId) {
    return 'wizard.validation.datalogger_required';
  }
  if (!EXTERNAL_ID_RE.test(data.externalId ?? '')) return 'wizard.validation.external_id_invalid';
  return null;
}

export type PointParse =
  | { status: 'ok'; point: Point }
  | { status: 'incomplete' }
  | { status: 'invalid' };

function parseCoordinate(raw: string, limit: number): number | 'empty' | 'invalid' {
  const text = raw.trim().replace(',', '.');
  if (text === '') return 'empty';
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(text)) return 'invalid';
  const value = Number(text);
  return Number.isFinite(value) && Math.abs(value) <= limit ? value : 'invalid';
}

/** Typed latitude/longitude -> GeoJSON Point ([lon, lat]). Accepts a decimal comma. */
export function parsePointCoordinates(latText: string, lonText: string): PointParse {
  const lat = parseCoordinate(latText, 90);
  const lon = parseCoordinate(lonText, 180);
  if (lat === 'invalid' || lon === 'invalid') return { status: 'invalid' };
  if (lat === 'empty' || lon === 'empty') return { status: 'incomplete' };
  return { status: 'ok', point: { type: 'Point', coordinates: [lon, lat] } };
}

/** i18n key of the geometry-step problem, or null. A `Device` needs a Point; other types are unchanged. */
export function validateGeometryStep(
  entityType: string | null,
  geometry: Geometry | null | undefined,
): string | null {
  if (entityType === 'Device' && geometry?.type !== 'Point') {
    return 'wizard.validation.point_required';
  }
  return null;
}
