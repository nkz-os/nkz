/**
 * Sensors API (entity-manager, through the api-gateway): dataloggers, sensor
 * registration and sensor profiles.
 *
 * SECURITY: a datalogger password is returned once (create, rotate). Callers keep
 * it only in the component that shows it and must never log or persist it.
 */
import api from './api';

export type DataloggerKind = 'datak' | 'generic_mqtt';
export type DataloggerStatus = 'active' | 'revoked';

export const DATALOGGER_KINDS: readonly DataloggerKind[] = ['datak', 'generic_mqtt'];

/** Roles the API lets set up dataloggers and the sensors on them (a Farmer gets 403). */
export const DATALOGGER_INSTALLER_ROLES = ['PlatformAdmin', 'TenantAdmin', 'TechnicalConsultant'];

export interface DataloggerDevice {
  externalId: string;
  deviceUrn: string;
}

export interface Datalogger {
  id: string;
  name: string;
  kind: DataloggerKind;
  username: string;
  status: DataloggerStatus;
  created_at?: string | null;
  devices: DataloggerDevice[];
}

/** MQTT endpoint as the backend answers it: either usable, or the reason it is not. */
export interface MqttEndpoint {
  configured?: boolean;
  reason?: string;
  host?: string;
  port?: number;
  protocol?: string;
  username?: string;
  password?: string | null;
  apikey?: string;
}

export interface CreatedDatalogger {
  id: string;
  mqtt: MqttEndpoint;
  devices: DataloggerDevice[];
}

export interface DataloggerConfig {
  datalogger_id: string;
  name: string;
  mqtt: MqttEndpoint;
  devices: Array<{
    externalId: string;
    name: string;
    topic: string;
    measurements: Array<{ key: string; sdmAttribute: string | null; unit: string | null }>;
  }>;
}

export interface RegisterSensorRequest {
  external_id: string;
  name: string;
  profile: string;
  location: { lat: number; lon: number };
  /** Owner first; at least one AgriParcel URN. */
  controlledAsset: string[];
  datalogger_id: string;
  healthConfig?: Record<string, unknown>;
  calibrationConfig?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface RegisteredSensor {
  entity_id: string;
  topic: string;
  datalogger_id: string;
  needs_rotation?: boolean;
}

export interface SensorProfile {
  code: string;
  name: string;
  description?: string;
  sdm_entity_type?: string;
  sdm_attributes?: string[];
  health_defaults?: Record<string, { minValid?: number; maxValid?: number; maxStagnantHours?: number }>;
}

const BASE = '/api/sensors';
const enc = encodeURIComponent;

export async function listDataloggers(): Promise<Datalogger[]> {
  const res = await api.get(`${BASE}/dataloggers`);
  return Array.isArray(res.data) ? res.data : [];
}

export async function createDatalogger(name: string, kind: DataloggerKind): Promise<CreatedDatalogger> {
  const res = await api.post(`${BASE}/dataloggers`, { name, kind });
  return res.data;
}

export async function getDataloggerConfig(id: string): Promise<DataloggerConfig> {
  const res = await api.get(`${BASE}/dataloggers/${enc(id)}/config`);
  return res.data;
}

export async function rotateDatalogger(id: string): Promise<{ password: string }> {
  const res = await api.post(`${BASE}/dataloggers/${enc(id)}/rotate`);
  return res.data;
}

export async function revokeDatalogger(id: string): Promise<void> {
  await api.post(`${BASE}/dataloggers/${enc(id)}/revoke`);
}

export async function enableDatalogger(id: string): Promise<void> {
  await api.post(`${BASE}/dataloggers/${enc(id)}/enable`);
}

export async function deleteDatalogger(id: string): Promise<void> {
  await api.delete(`${BASE}/dataloggers/${enc(id)}`);
}

export async function registerSensor(body: RegisterSensorRequest): Promise<RegisteredSensor> {
  const res = await api.post(`${BASE}/register`, body);
  return res.data;
}

export async function listSensorProfiles(): Promise<SensorProfile[]> {
  const res = await api.get(`${BASE}/profiles`);
  return Array.isArray(res.data?.profiles) ? res.data.profiles : [];
}

/** Server error text of a failed call, or the fallback. */
export function apiErrorMessage(err: unknown, fallback: string): string {
  const e = err as { response?: { data?: { error?: unknown } }; message?: unknown };
  const serverText = e?.response?.data?.error;
  const step = (e?.response?.data as { step?: unknown } | undefined)?.step;
  if (typeof serverText === 'string' && serverText) {
    return typeof step === 'string' && step ? `${serverText} (${step})` : serverText;
  }
  return typeof e?.message === 'string' && e.message ? e.message : fallback;
}
