import api from '@/services/api';
import type { FleetFormData } from '../types';
import type { RobotCredentials } from '../RobotCredentialsModal';

export interface FleetSubmitResult {
  robotCredentials: RobotCredentials | null;
}

export async function submitFleet(
  entityType: string,
  formData: FleetFormData,
): Promise<FleetSubmitResult> {
  // AutonomousMobileRobot: dedicated provisioning endpoint that returns VPN credentials
  if (entityType === 'AutonomousMobileRobot') {
    const response = await api.provisionRobot({
      name: formData.name,
      location: formData.geometry ? { type: 'GeoProperty', value: formData.geometry } : undefined,
      robotType: formData.robotType,
      manufacturer: formData.manufacturer,
      serialNumber: formData.serialNumber,
      icon: formData.iconUrl,
      ref3DModel: formData.model3DUrl,
      modelScale: formData.modelScale,
      modelRotation: formData.modelRotation,
    });
    return { robotCredentials: response.credentials ?? null };
  }

  // ManufacturingMachines, operations — generic SDM entity
  const entityId = `urn:ngsi-ld:${entityType}:current:${Date.now()}`;

  const entity: Record<string, unknown> = {
    id: entityId,
    type: entityType,
    name: { type: 'Property', value: formData.name },
  };

  if (formData.description) {
    entity.description = { type: 'Property', value: formData.description };
  }

  if (formData.geometry) {
    entity.location = { type: 'GeoProperty', value: formData.geometry };
  }

  if (formData.manufacturer) {
    entity.manufacturer = { type: 'Property', value: formData.manufacturer };
  }
  if (formData.serialNumber) {
    entity.serialNumber = { type: 'Property', value: formData.serialNumber };
  }
  if (formData.isobusCompatible !== undefined) {
    entity.isobusCompatible = { type: 'Property', value: formData.isobusCompatible };
  }
  if (entityType === 'ManufacturingMachine' && formData.machineRole) {
    entity.category = { type: 'Property', value: formData.machineRole };
  }
  if (formData.trackWidth !== undefined) {
    entity.trackWidth = { type: 'Property', value: formData.trackWidth };
  }
  if (formData.wheelbase !== undefined) {
    entity.wheelbase = { type: 'Property', value: formData.wheelbase };
  }
  if (formData.gpsOffsetX !== undefined) {
    entity.gpsOffsetX = { type: 'Property', value: formData.gpsOffsetX };
  }
  if (formData.gpsOffsetY !== undefined) {
    entity.gpsOffsetY = { type: 'Property', value: formData.gpsOffsetY };
  }
  if (formData.gpsOffsetZ !== undefined) {
    entity.gpsOffsetZ = { type: 'Property', value: formData.gpsOffsetZ };
  }
  if (formData.hitchType) {
    entity.hitchType = { type: 'Property', value: formData.hitchType };
  }
  if (formData.hitchOffsetX !== undefined) {
    entity.hitchOffsetX = { type: 'Property', value: formData.hitchOffsetX };
  }
  if (formData.implementLength !== undefined) {
    entity.implementLength = { type: 'Property', value: formData.implementLength };
  }
  if (formData.implementOffsetX !== undefined) {
    entity.implementOffsetX = { type: 'Property', value: formData.implementOffsetX };
  }
  if (formData.implementWidth !== undefined) {
    entity.implementWidth = { type: 'Property', value: formData.implementWidth };
  }
  if (formData.steeringType) {
    entity.steeringType = { type: 'Property', value: formData.steeringType };
  }
  if (formData.steeringAxles) {
    entity.steeringAxles = { type: 'Property', value: formData.steeringAxles };
  }

  if (formData.iconUrl) {
    entity.icon2d = { type: 'Property', value: formData.iconUrl };
  } else if (formData.defaultIconKey) {
    entity.icon2d = { type: 'Property', value: `icon:${formData.defaultIconKey}` };
  }
  if (formData.model3DUrl) {
    entity.ref3DModel    = { type: 'Property', value: formData.model3DUrl };
    entity.modelScale    = { type: 'Property', value: formData.modelScale ?? 1 };
    entity.modelRotation = { type: 'Property', value: formData.modelRotation ?? [0, 0, 0] };
  }

  for (const [k, v] of Object.entries(formData.additionalAttributes)) {
    if (v !== '' && v !== null && v !== undefined) {
      entity[k] = { type: 'Property', value: v };
    }
  }

  await api.createSDMEntity(entityType, entity);
  return { robotCredentials: null };
}
