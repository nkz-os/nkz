/**
 * Canonical Orion-LD API prefix for browser same-origin requests.
 *
 * Both nekazari.robotika.cloud and nkz.robotika.cloud expose `/ngsi-ld/*`
 * via Traefik → api-gateway → Orion-LD. The `/api/ngsi-ld/*` gateway alias
 * exists for backward compatibility on the API host only; federated modules
 * must use this prefix so requests never fall through to the SPA shell.
 */
export const ORION_LD_PREFIX = '/ngsi-ld';

export function orionEntityPath(entityId: string, type?: string): string {
  const q = type ? `?type=${encodeURIComponent(type)}` : '';
  return `${ORION_LD_PREFIX}/v1/entities/${encodeURIComponent(entityId)}${q}`;
}

export function orionEntitiesPath(params: URLSearchParams): string {
  return `${ORION_LD_PREFIX}/v1/entities?${params.toString()}`;
}

export function orionEntityAttrsPath(entityId: string): string {
  return `${ORION_LD_PREFIX}/v1/entities/${encodeURIComponent(entityId)}/attrs`;
}
