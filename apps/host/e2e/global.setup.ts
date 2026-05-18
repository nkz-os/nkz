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

  // 1. Go directly to Keycloak OIDC auth endpoint
  const loginUrl =
    `${BASE_URL}/auth/realms/nekazari/protocol/openid-connect/auth` +
    `?client_id=nekazari-frontend` +
    `&redirect_uri=${encodeURIComponent(BASE_URL + '/')}` +
    `&response_type=code` +
    `&scope=openid`;

  await page.goto(loginUrl, { waitUntil: 'networkidle', timeout: 30000 });

  // 2. Wait for Keycloak login form. Try multiple possible selectors
  //    (custom themes may vary, but Keycloak base always has these).
  const usernameSelector = '#username, input[name="username"], input[type="text"]';
  await page.waitForSelector(usernameSelector, { timeout: 15000 });
  await page.fill(usernameSelector, 'demo@nekazari.local');
  await page.fill('#password, input[name="password"], input[type="password"]', 'Demo1234!');
  await page.click('#kc-login, input[type="submit"], button[type="submit"]');

  // 3. Wait for redirect back to the app
  await page.waitForURL((url) => !url.toString().includes('/auth/'), { timeout: 20000 });
  await page.waitForLoadState('networkidle', { timeout: 10000 });
  await page.waitForTimeout(3000);

  // 4. Save browser state for reuse
  await page.context().storageState({ path: AUTH_FILE });

  await browser.close();
}

export default globalSetup;
