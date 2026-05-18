/**
 * Playwright global setup — log in via Keycloak UI and persist the
 * httpOnly session cookie so every test starts authenticated.
 */
import { chromium } from '@playwright/test';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE_URL = process.env['E2E_BASE_URL'] || 'http://localhost:3000';
const AUTH_FILE = path.join(__dirname, '.auth', 'storageState.json');

async function globalSetup() {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  // 1. Go directly to Keycloak login, then let it redirect back to the app.
  //    This mirrors what the Keycloak JS adapter does internally.
  const loginUrl =
    `${BASE_URL}/auth/realms/nekazari/protocol/openid-connect/auth` +
    `?client_id=nekazari-frontend` +
    `&redirect_uri=${encodeURIComponent(BASE_URL + '/')}` +
    `&response_type=code` +
    `&scope=openid`;

  await page.goto(loginUrl, { waitUntil: 'domcontentloaded' });

  // 2. Fill Keycloak login form
  await page.waitForSelector('#username', { timeout: 10000 });
  await page.fill('#username', 'demo@nekazari.local');
  await page.fill('#password', 'Demo1234!');
  await page.click('#kc-login');

  // 3. Wait for redirect back to the app (URL no longer contains /auth/)
  await page.waitForURL((url) => !url.toString().includes('/auth/'), { timeout: 15000 });
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(3000);

  // 4. Save browser state (cookies, localStorage) for reuse
  await page.context().storageState({ path: AUTH_FILE });

  await browser.close();
}

export default globalSetup;
