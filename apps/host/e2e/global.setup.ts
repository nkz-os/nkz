/**
 * Playwright global setup — authenticate via the host app's Keycloak flow.
 * We navigate to a protected route; the app redirects to Keycloak with PKCE.
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

  // Navigate to a protected route — the Keycloak JS adapter will redirect
  // to the login form with correct PKCE params. The landing page (/) may be
  // public and not trigger auth.
  await page.goto(BASE_URL + '/entities', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);

  // Should now be on the Keycloak login page
  const url = page.url();
  if (!url.includes('/auth/')) {
    await saveDebug(page, 'no-redirect');
    throw new Error(`Expected Keycloak redirect, got: ${url}`);
  }

  // Fill login form
  await page.waitForSelector('#username, input[name="username"]', { timeout: 15000 });
  await page.fill('#username, input[name="username"]', 'demo@nekazari.local');
  await page.fill('#password, input[name="password"]', 'Demo1234!');
  await page.click('#kc-login, input[type="submit"]');

  // Wait for redirect back to app + session cookie
  await page.waitForURL((u) => !u.toString().includes('/auth/'), { timeout: 20000 });
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(3000);

  // Save browser state
  await page.context().storageState({ path: AUTH_FILE });
  await browser.close();
}

async function saveDebug(page: any, reason: string) {
  try {
    fs.mkdirSync(DEBUG_DIR, { recursive: true });
    await page.screenshot({ path: path.join(DEBUG_DIR, `${reason}.png`), fullPage: true });
    fs.writeFileSync(path.join(DEBUG_DIR, `${reason}.html`), await page.content());
    console.error(`[setup] ${reason}: URL=${page.url()}, title="${await page.title()}"`);
  } catch (_ignored) { /* don't hide original error */ }
}

export default globalSetup;
