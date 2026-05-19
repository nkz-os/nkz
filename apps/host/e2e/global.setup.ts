/**
 * Playwright global setup placeholder.
 *
 * Browser-based Keycloak auth is deferred — the docker-compose stack has
 * an issuer/audience mismatch between the public Keycloak URL (localhost:3000)
 * and the api-gateway's internal JWKS validation. Once resolved, this file
 * should log in via the host app's Keycloak flow and persist the session cookie.
 *
 * For now, E2E tests verify unauthenticated paths: stack health, API, MinIO.
 */
export default async function globalSetup() {
  // no-op — auth deferred
}
