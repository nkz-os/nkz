/**
 * Playwright global setup — log in via the app's own Keycloak flow.
 * The host app's Keycloak JS adapter generates PKCE params correctly;
 * we just navigate to / and let it redirect us.
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

  // 1. Go to the app. The Keycloak JS adapter will auto-redirect to
  //    Keycloak with correct PKCE params, or show a landing page with a
  //    login link that leads there.
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3000);

  // 2. If we're not already on the Keycloak login page, find and click a
  //    login button/link.
  if (!page.url().includes('/auth/')) {
    const loginBtn = page.locator(
      'a[href*="auth"], a:has-text("Login"), a:has-text("Iniciar"), a:has-text("Entrar"), button:has-text("Login")',
    );
    const count = await loginBtn.count();
    if (count > 0) {
      await loginBtn.first().click();
    }
    // Wait for the redirect to Keycloak
    await page.waitForURL((url) => url.toString().includes('/auth/'), { timeout: 15000 });
  }

  // 3. Fill Keycloak login form
  const usernameSelector = '#username, input[name="username"]';
  try {
    await page.waitForSelector(usernameSelector, { timeout: 20000 });
  } catch (_err) {
    await saveDebug(page, 'no-login-form');
    throw new Error(`Login form not found at ${page.url()}`);
  }

  await page.fill(usernameSelector, 'demo@nekazari.local');
  await page.fill('#password, input[name="password"]', 'Demo1234!');
  await page.click('#kc-login, input[type="submit"]');

  // 4. Wait for redirect back to the app and session cookie
  await page.waitForURL((url) => !url.toString().includes('/auth/'), { timeout: 20000 });
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(3000);

  // 5. Save browser state
  await page.context().storageState({ path: AUTH_FILE });
  await browser.close();
}

async function saveDebug(page: any, reason: string) {
  try {
    fs.mkdirSync(DEBUG_DIR, { recursive: true });
    await page.screenshot({ path: path.join(DEBUG_DIR, `${reason}.png`), fullPage: true });
    fs.writeFileSync(path.join(DEBUG_DIR, `${reason}.html`), await page.content());
  } catch (_ignored) { /* don't hide original error */ }
}

export default globalSetup;
