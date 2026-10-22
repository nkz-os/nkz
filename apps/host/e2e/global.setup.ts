import { chromium, type FullConfig } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

/**
 * Playwright global setup — authenticated session bootstrap.
 *
 * Logs in once through the real Keycloak redirect flow (username/password
 * form, PKCE code exchange, BFF session creation) and persists the
 * resulting storage state — including the httpOnly `nkz_token` cookie — to
 * disk. Individual spec files opt in with `test.use({ storageState: AUTH_STATE_PATH })`
 * rather than this being wired globally, since some specs (smoke.spec.ts)
 * intentionally exercise the unauthenticated path.
 *
 * History: this file used to be a no-op with a comment claiming the
 * blocker was an issuer/audience mismatch between the public Keycloak URL
 * and the api-gateway's JWKS validation. That diagnosis was wrong. The
 * actual defects were both in docker/nginx-compose.conf (local dev only,
 * fixed 2026-09-18):
 *   1. it never served or injected /__nkz_runtime__.js into <head>, so
 *      window.__ENV__ stayed empty and the app fell back to its compiled
 *      defaults — i.e. it authenticated against PRODUCTION Keycloak;
 *   2. its /auth/ proxy block forwarded nginx's internal listen port (80)
 *      as X-Forwarded-Port instead of the host-published port (3000), so
 *      Keycloak (KC_PROXY=edge) issued tokens whose `iss` omitted the
 *      port and never matched the gateway's issuer whitelist.
 * With both fixed, this flow logs in end-to-end against the local
 * docker-compose stack.
 */

const BASE_URL = process.env['E2E_BASE_URL'] || 'http://localhost:3000';

// Local docker-compose seeds this exact account (see ADMIN_PASSWORD in
// docker-compose.yml and docker/init-keycloak-users.sh) — the default here
// mirrors that existing, already-public local-dev fixture. Override both
// for any other target stack.
const ADMIN_EMAIL = process.env['E2E_ADMIN_EMAIL'] || 'admin@nekazari.local';
const ADMIN_PASSWORD = process.env['E2E_ADMIN_PASSWORD'] || 'Admin1234!';

export const AUTH_STATE_PATH = path.resolve(process.cwd(), 'e2e/.auth/admin.json');

export default async function globalSetup(_config: FullConfig) {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  await page.goto(`${BASE_URL}/login`, { waitUntil: 'networkidle', timeout: 30_000 });

  // Proof this is hitting the LOCAL stack: the redirect must land on
  // {BASE_URL}/auth/realms/... (proxied to the local Keycloak container),
  // never a production auth host.
  await page.waitForURL(/\/auth\/realms\//, { timeout: 15_000 });
  const kcUrl = page.url();
  if (!kcUrl.startsWith(`${BASE_URL}/auth/`)) {
    throw new Error(
      `[global.setup] Expected to land on local Keycloak under ${BASE_URL}/auth/, got: ${kcUrl}`
    );
  }

  await page.fill('#username', ADMIN_EMAIL);
  await page.fill('#password', ADMIN_PASSWORD);

  const [sessionResponse] = await Promise.all([
    page.waitForResponse(
      (res) => res.url().includes('/api/auth/session') && res.request().method() === 'POST',
      { timeout: 20_000 }
    ),
    page.click('#kc-login'),
  ]);

  if (!sessionResponse.ok()) {
    throw new Error(
      `[global.setup] BFF session creation failed: POST /api/auth/session -> ${sessionResponse.status()}`
    );
  }

  const cookies = await context.cookies();
  if (!cookies.some((c) => c.name === 'nkz_token')) {
    throw new Error('[global.setup] Login flow completed but nkz_token cookie was not set.');
  }

  fs.mkdirSync(path.dirname(AUTH_STATE_PATH), { recursive: true });
  await context.storageState({ path: AUTH_STATE_PATH });

  await browser.close();
}
