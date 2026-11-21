/**
 * Runtime API origin injected by the host page (`window.__ENV__.VITE_API_URL`),
 * falling back to the build-time value; empty means same origin (dev).
 *
 * The session cookie is host-only on the API host, so a gateway-bound request
 * sent to the frontend host carries no credentials and gets a 401.
 */
export function apiOrigin(): string {
  // Read through a local shape: the host declares its own `window.__ENV__` type.
  const env = typeof window !== 'undefined'
    ? (window as unknown as { __ENV__?: { VITE_API_URL?: string } }).__ENV__
    : undefined;
  const fromWindow = env?.VITE_API_URL;
  const fromBuild = (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_API_URL;
  return String(fromWindow ?? fromBuild ?? '').replace(/\/+$/, '');
}

/** Absolute API URL for a root-relative path; other URLs are returned unchanged. */
export function toApiUrl(path: string): string {
  return path.startsWith('/') && !path.startsWith('//') ? `${apiOrigin()}${path}` : path;
}
