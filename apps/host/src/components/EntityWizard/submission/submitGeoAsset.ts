import { parcelApi } from '@/services/parcelApi';
import api from '@/services/api';
import type { GeoAssetFormData } from '../types';
import type { PlacementState } from '@/machines/placementMachine';
import type { MqttCredentials } from '../MqttCredentialsModal';

export interface GeoAssetSubmitResult {
  mqttCredentials: MqttCredentials | null;
}

export async function submitGeoAsset(
  entityType: string,
  formData: GeoAssetFormData,
  placementState: PlacementState,
): Promise<GeoAssetSubmitResult> {
  // AgriParcel uses the dedicated parcel API (NGSI-LD structure with cadastral fields)
  if (entityType === 'AgriParcel') {
    await parcelApi.createParcel({
      name: formData.name,
      geometry: formData.geometry as any,
      municipality: formData.municipality ?? '',
      province: formData.province ?? '',
      cadastralReference: formData.cadastralReference,
      cropType: formData.cropType ?? '',
      notes: formData.description,
      ndviEnabled: true,
    });
    return { mqttCredentials: null };
  }

  // IoT provisioning path — when a DeviceProfile is selected (PhotovoltaicInstallation, AgriEnergyTracker)
  const useIoT = formData.deviceProfileId != null;

  if (useIoT) {
    // Build flat body for SDM Integration (same format as submitIoTSensor)
    const body: Record<string, unknown> = {
      name: formData.name,
    };
    if (formData.description) {
      body.description = formData.description;
    }
    // Location
    if ((placementState.mode === 'stamp' || placementState.mode === 'array') && placementState.stampedInstances.length > 0) {
      body.location = {
        type: 'GeoProperty',
        value: {
          type: 'MultiPoint',
          coordinates: placementState.stampedInstances.map(i => [i.lng, i.lat]),
        },
      };
    } else if (formData.geometry) {
      body.location = { type: 'GeoProperty', value: formData.geometry };
    }
    // Parcel association
    if (!formData.isSubdivision && formData.parentEntity?.type === 'AgriParcel') {
      body.refAgriParcel = { type: 'Relationship', object: formData.parentEntity.id };
    }
    // DeviceProfile
    const profileUrn = formData.deviceProfileId!.startsWith('urn:')
      ? formData.deviceProfileId!
      : `urn:ngsi-ld:DeviceProfile:${formData.deviceProfileId!}`;
    body.refDeviceProfile = { type: 'Relationship', object: profileUrn };
    // Visualization
    if (formData.model3DUrl) {
      body.ref3DModel = formData.model3DUrl;
      body.modelScale = formData.modelScale ?? 1;
      body.modelRotation = formData.modelRotation ?? [0, 0, 0];
    }
    // Panel dimensions
    if (formData.panelWidth !== undefined) body.panelWidth = formData.panelWidth;
    if (formData.panelLength !== undefined) body.panelLength = formData.panelLength;
    if (formData.panelHeight !== undefined) body.panelHeight = formData.panelHeight;
    // Additional SDM attributes
    for (const [k, v] of Object.entries(formData.additionalAttributes)) {
      if (v !== '' && v !== null && v !== undefined) body[k] = v;
    }

    const result = await api.createSDMIoTEntity(entityType, body);

    // Extract MQTT credentials
    let mqttCredentials: MqttCredentials | null = null;
    const mqtt = result.mqtt_credentials;
    if (mqtt) {
      mqttCredentials = {
        host: mqtt.host ?? 'mosquitto-service',
        port: mqtt.port ?? 8883,
        protocol: mqtt.protocol ?? 'mqtts',
        api_key: mqtt.api_key ?? result.api_key,
        device_id: mqtt.device_id ?? '',
        topics: {
          publish_data: mqtt.topics?.publish_data ?? '',
          publish_data_json: mqtt.topics?.publish_data_json ?? '',
          commands: mqtt.topics?.commands ?? '',
        },
        example_payload: mqtt.example_payload,
        warning: mqtt.warning,
      };
    }
    return { mqttCredentials };
  }

  // ── Legacy path: create entity directly in Orion-LD (no IoT provisioning) ──

  const entityId = `urn:ngsi-ld:${entityType}:current:${Date.now()}`;

  const entity: Record<string, unknown> = {
    id: entityId,
    type: entityType,
    name: { type: 'Property', value: formData.name },
  };

  if (formData.description) {
    entity.description = { type: 'Property', value: formData.description };
  }

  // Location: stamp mode builds MultiPoint from instances
  if ((placementState.mode === 'stamp' || placementState.mode === 'array') && placementState.stampedInstances.length > 0) {
    entity.location = {
      type: 'GeoProperty',
      value: {
        type: 'MultiPoint',
        coordinates: placementState.stampedInstances.map(i => [i.lng, i.lat]),
      },
    };
  } else if (formData.geometry) {
    entity.location = { type: 'GeoProperty', value: formData.geometry };
  }

  // Parent relationship
  if (formData.isSubdivision && formData.parentEntity) {
    entity.refParent = { type: 'Relationship', object: formData.parentEntity.id };
  }

  // Parcel association for energy-related types (AgriEnergy module integration)
  if (!formData.isSubdivision && formData.parentEntity?.type === 'AgriParcel') {
    entity.refAgriParcel = { type: 'Relationship', object: formData.parentEntity.id };
  }

  // Visualization
  if (formData.iconUrl) {
    entity.icon2d = { type: 'Property', value: formData.iconUrl };
  } else if (formData.defaultIconKey) {
    entity.icon2d = { type: 'Property', value: `icon:${formData.defaultIconKey}` };
  }
  if (formData.model3DUrl) {
    entity.ref3DModel   = { type: 'Property', value: formData.model3DUrl };
    entity.modelScale   = { type: 'Property', value: formData.modelScale ?? 1 };
    entity.modelRotation = { type: 'Property', value: formData.modelRotation ?? [0, 0, 0] };
  }

  // AgriEnergyTracker: inject tilt/azimuth/modelRotation from array settings
  if (entityType === 'AgriEnergyTracker' && placementState.mode === 'array') {
    const { bearing, tilt, nominalPower } = placementState.arraySettings;
    entity.tilt      = { type: 'Property', value: tilt };
    entity.azimuth   = { type: 'Property', value: bearing };
    entity.modelRotation = { type: 'Property', value: [bearing, -tilt, 0] };
    if (!formData.additionalAttributes.panelDimension) {
      entity.panelDimension = { type: 'Property', value: { width: 2.0, length: 4.0, thickness: 0.04 } };
    }
    if (!formData.additionalAttributes.NominalPower) {
      entity.NominalPower = { type: 'Property', value: nominalPower };
    }
  }

  // PhotovoltaicInstallation: submit panel dimensions
  if (entityType === 'PhotovoltaicInstallation') {
    if (formData.panelWidth !== undefined) {
      entity.panelWidth = { type: 'Property', value: formData.panelWidth };
    }
    if (formData.panelLength !== undefined) {
      entity.panelLength = { type: 'Property', value: formData.panelLength };
    }
    if (formData.panelHeight !== undefined) {
      entity.panelHeight = { type: 'Property', value: formData.panelHeight };
    }
  }

  // Dynamic SDM attributes
  for (const [k, v] of Object.entries(formData.additionalAttributes)) {
    if (v !== '' && v !== null && v !== undefined) {
      entity[k] = { type: 'Property', value: v };
    }
  }

  await api.createSDMEntity(entityType, entity);
  return { mqttCredentials: null };
}
