/**
 * Playwright global setup — log in via Keycloak UI and persist the
 * httpOnly session cookie so every test starts authenticated.
 */
import { chromium } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE_URL = process.env['E2E_BASE_URL'] || 'http://localhost:3000';
const AUTH_FILE = path.join(__dirname, '.auth', 'storageState.json');
const DEBUG_DIR = path.join(__dirname, '..', 'playwright-report', 'setup-debug');

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

  try {
    await page.goto(loginUrl, { waitUntil: 'load', timeout: 30000 });
  } catch (_err) {
    await saveDebug(page, 'goto-failed');
    throw _err;
  }

  // 2. Wait for Keycloak login form
  const usernameSelector = '#username, input[name="username"]';
  try {
    await page.waitForSelector(usernameSelector, { timeout: 20000 });
  } catch (_err) {
    await saveDebug(page, 'no-login-form');
    throw new Error(
      `Keycloak login form not found. URL: ${page.url()}. Debug saved to ${DEBUG_DIR}`,
    );
  }

  await page.fill(usernameSelector, 'demo@nekazari.local');
  await page.fill('#password, input[name="password"]', 'Demo1234!');
  await page.click('#kc-login, input[type="submit"]');

  // 3. Wait for redirect back to the app
  await page.waitForURL((url) => !url.toString().includes('/auth/'), { timeout: 20000 });
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(3000);

  // 4. Save browser state for reuse
  await page.context().storageState({ path: AUTH_FILE });

  await browser.close();
}

async function saveDebug(page: any, reason: string) {
  try {
    fs.mkdirSync(DEBUG_DIR, { recursive: true });
    await page.screenshot({ path: path.join(DEBUG_DIR, `${reason}.png`), fullPage: true });
    fs.writeFileSync(path.join(DEBUG_DIR, `${reason}.html`), await page.content());
    console.error(`[setup] Debug saved: ${DEBUG_DIR}/${reason}.{png,html}`);
    console.error(`[setup] Current URL: ${page.url()}`);
    console.error(`[setup] Page title: ${await page.title()}`);
  } catch (_ignored) {
    // don't let debug saving hide the original error
  }
}

export default globalSetup;
