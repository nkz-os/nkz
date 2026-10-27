import api, { invalidateEntityCache } from '@/services/api';
import { deviceLinks } from '@/utils/parcelLinks';
import type { IoTSensorFormData } from '../types';
import {
  createDatalogger,
  registerSensor,
  type MqttEndpoint,
  type RegisterSensorRequest,
} from '@/services/dataloggersApi';
import type { MqttCredentials } from '../MqttCredentialsModal';

/** A datalogger created by this submit. Its password is shown once and kept nowhere else. */
export interface NewDataloggerResult {
  id: string;
  name: string;
  entityId: string;
  topic: string;
  mqtt: MqttEndpoint;
}

export interface IoTSubmitResult {
  /** Credentials of the legacy (non-Device) flow. */
  mqttCredentials: MqttCredentials | null;
  /** Set only when a new datalogger was created for a Device. */
  newDatalogger: NewDataloggerResult | null;
  /** The datalogger's broker account is missing: its password must be regenerated. */
  needsRotation: boolean;
}

/** A registration that failed after its datalogger had been created. */
export class DataloggerRegistrationError extends Error {
  readonly createdDataloggerId: string;
  readonly response?: unknown;
  constructor(cause: unknown, createdDataloggerId: string) {
    super(cause instanceof Error ? cause.message : 'Registration failed');
    this.name = 'DataloggerRegistrationError';
    this.createdDataloggerId = createdDataloggerId;
    this.response = (cause as { response?: unknown } | null)?.response;
  }
}

/** The endpoint to show: a password only when the broker endpoint is usable. */
function endpointToShow(mqtt: MqttEndpoint): MqttEndpoint {
  if (mqtt.configured === false) {
    return { configured: false, reason: mqtt.reason, username: mqtt.username };
  }
  return {
    configured: true,
    host: mqtt.host,
    port: mqtt.port,
    protocol: mqtt.protocol,
    username: mqtt.username,
    password: mqtt.password,
  };
}

/**
 * Register a Device on a datalogger (existing, or created here) through the
 * sensors API. The API writes the entity, the IoT Agent device and the broker
 * ACL, and undoes them if one fails.
 */
async function submitDevice(formData: IoTSensorFormData): Promise<IoTSubmitResult> {
  if (!formData.deviceProfileId) throw new Error('DeviceProfile is required for IoT sensors');
  if (formData.geometry?.type !== 'Point') throw new Error('A Device requires a point location');
  const [lon, lat] = formData.geometry.coordinates;

  let dataloggerId = formData.dataloggerId ?? '';
  let created: { id: string; mqtt: MqttEndpoint } | null = null;
  if (formData.dataloggerMode === 'new') {
    created = await createDatalogger(
      (formData.newDataloggerName ?? '').trim(),
      formData.newDataloggerKind ?? 'datak',
    );
    dataloggerId = created.id;
  }

  const body: RegisterSensorRequest = {
    external_id: formData.externalId ?? '',
    name: formData.name,
    profile: formData.deviceProfileId,
    location: { lat, lon },
    controlledAsset: deviceLinks(formData.ownerParkId ?? null, formData.parcels.map((p) => p.id)),
    datalogger_id: dataloggerId,
  };
  if (formData.healthConfig) body.healthConfig = formData.healthConfig;
  if (formData.calibrationConfig) body.calibrationConfig = formData.calibrationConfig;
  if (formData.description) body.metadata = { description: formData.description };

  let registered;
  try {
    registered = await registerSensor(body);
  } catch (err) {
    if (created) throw new DataloggerRegistrationError(err, created.id);
    throw err;
  }
  invalidateEntityCache('Device');

  return {
    mqttCredentials: null,
    needsRotation: !created && registered.needs_rotation === true,
    newDatalogger: created
      ? {
          id: created.id,
          name: (formData.newDataloggerName ?? '').trim(),
          entityId: registered.entity_id,
          topic: registered.topic,
          mqtt: endpointToShow(created.mqtt),
        }
      : null,
  };
}

/**
 * Submit an IoT sensor. A `Device` is registered through the sensors API
 * (see submitDevice); every other sensor type goes through SDM Integration:
 *
 * SDM Integration handles the full workflow in one call:
 *   1. Creates the NGSI-LD entity in Orion-LD
 *   2. Provisions the device in the IoT Agent (MQTT transport)
 *   3. Returns MQTT credentials (api_key, topics, host/port)
 *
 * The returned credentials are shown once and cannot be recovered later.
 */
export async function submitIoTSensor(
  entityType: string,
  formData: IoTSensorFormData,
): Promise<IoTSubmitResult> {
  if (entityType === 'Device') return submitDevice(formData);

  // Build the body for SDM Integration.
  // SDM Integration auto-generates the entity ID and wraps properties in NGSI-LD format.
  const body: Record<string, unknown> = {
    name: formData.name,
  };

  // Attach sensor health & calibration configuration if present
  if (formData.healthConfig) {
    body.healthConfig = formData.healthConfig;
  }
  if (formData.calibrationConfig) {
    body.calibrationConfig = formData.calibrationConfig;
  }

  if (formData.description) {
    body.description = formData.description;
  }

  if (formData.geometry) {
    body.location = {
      type: 'GeoProperty',
      value: formData.geometry,
    };
  }

  // hasDeviceProfile as NGSI-LD Relationship (mandatory for IoT sensors)
  if (!formData.deviceProfileId) {
    throw new Error('DeviceProfile is required for IoT sensors');
  }
  const profileUrn = formData.deviceProfileId.startsWith('urn:')
    ? formData.deviceProfileId
    : `urn:ngsi-ld:DeviceProfile:${formData.deviceProfileId}`;
  body.hasDeviceProfile = {
    type: 'Relationship',
    object: profileUrn,
  };

  if (formData.iconUrl) {
    body.icon2d = formData.iconUrl;
  } else if (formData.defaultIconKey) {
    body.icon2d = `icon:${formData.defaultIconKey}`;
  }

  // Dynamic SDM attributes (flat values — SDM Integration wraps them)
  for (const [k, v] of Object.entries(formData.additionalAttributes)) {
    if (v !== '' && v !== null && v !== undefined) {
      body[k] = v;
    }
  }

  // Single call: entity creation + IoT Agent provisioning + MQTT credentials
  const result = await api.createSDMIoTEntity(entityType, body);

  // Drop the cached list so the asset tree shows the new entity
  invalidateEntityCache(entityType);

  // Extract MQTT credentials from SDM Integration response
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
        commands: mqtt.topics?.commands ?? '',
      },
      example_payload: mqtt.example_payload,
      warning: mqtt.warning,
    };
  }

  return { mqttCredentials, newDatalogger: null, needsRotation: false };
}
