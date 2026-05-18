/**
 * Playwright global setup — authenticate once via Keycloak password grant
 * and persist the httpOnly cookie so every test starts authenticated.
 */
import { request } from '@playwright/test';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const BASE_URL = process.env['E2E_BASE_URL'] || 'http://localhost:3000';
const AUTH_FILE = path.join(__dirname, '.auth', 'storageState.json');

async function globalSetup() {
  const context = await request.newContext({ baseURL: BASE_URL });

  // 1. Obtain a Keycloak access token via Resource Owner Password Grant
  const tokenRes = await context.post(
    '/auth/realms/nekazari/protocol/openid-connect/token',
    {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      data: new URLSearchParams({
        client_id: 'nekazari-frontend',
        grant_type: 'password',
        username: 'demo@nekazari.local',
        password: 'Demo1234!',
      }),
    },
  );

  if (!tokenRes.ok()) {
    throw new Error(
      `Keycloak token request failed [${tokenRes.status()}]: ${await tokenRes.text()}`,
    );
  }

  const { access_token } = await tokenRes.json();

  // 2. Exchange the token for a httpOnly session cookie
  const sessionRes = await context.post('/api/auth/session', {
    headers: { 'Content-Type': 'application/json' },
    data: { token: access_token },
  });

  if (!sessionRes.ok()) {
    throw new Error(
      `Session creation failed [${sessionRes.status()}]: ${await sessionRes.text()}`,
    );
  }

  // 3. Persist cookies (including httpOnly nkz_token) for reuse across tests
  const cookies = [
    ...(await context.storageState()).cookies,
    // Ensure the auth cookie is captured even if already present via storageState
  ];

  const fs = await import('fs/promises');
  await fs.mkdir(path.dirname(AUTH_FILE), { recursive: true });
  await fs.writeFile(
    AUTH_FILE,
    JSON.stringify({ cookies, origins: [] }, null, 2),
  );

  await context.dispose();
}

export default globalSetup;
