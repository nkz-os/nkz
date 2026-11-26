/** externalId accepted by the sensors API (it becomes a topic level and a URN segment). */
export const EXTERNAL_ID_RE = /^[A-Za-z0-9_.-]{1,64}$/;

/** Canonical tenant id (common/tenant_utils.normalize_tenant_id): lowercase letters, digits, inner hyphens. */
const TENANT_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/** `urn:ngsi-ld:Device:<tenant>:<externalId>`: the shape the registration route writes. */
export function isRegisteredDeviceUrn(urn: string): boolean {
  const parts = urn.split(':');
  return parts.length === 5 && parts[0] === 'urn' && parts[1] === 'ngsi-ld' && parts[2] === 'Device'
    && TENANT_RE.test(parts[3]) && EXTERNAL_ID_RE.test(parts[4]);
}

/** A usable externalId from a free-text name (accents folded, other characters to `_`). */
export function slugifyExternalId(name: string): string {
  return name
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .trim()
    .replace(/[^A-Za-z0-9_.-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 64);
}

const DEVICE_CATEGORY_IRI = 'https://smartdatamodels.org/dataModel.Device/deviceCategory';

/**
 * A gateway is a Device whose `deviceCategory` includes `network`. Orion returns a one-element
 * list as a plain string, and a stored value may arrive as `{ value }`: read every form. The
 * attribute itself may come under its full IRI when the read carried no `@context`.
 */
export function isGatewayDevice(entity: any): boolean {
  if (!entity || entity.type !== 'Device') return false;
  const raw = entity.deviceCategory ?? entity[DEVICE_CATEGORY_IRI];
  const value = raw && typeof raw === 'object' && !Array.isArray(raw) && 'value' in raw ? raw.value : raw;
  return (Array.isArray(value) ? value : [value]).includes('network');
}
