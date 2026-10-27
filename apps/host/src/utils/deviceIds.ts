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
