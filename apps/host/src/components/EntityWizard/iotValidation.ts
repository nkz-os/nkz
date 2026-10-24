import type { IoTSensorFormData } from './types';

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
