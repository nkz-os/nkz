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

  // 1. Navigate to the app — expect redirect to Keycloak login
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });

  // 2. The app either shows a landing page with a Login button or redirects
  //    straight to Keycloak. Click Login if present, otherwise we're already there.
  const loginLink = page.locator('a[href*="auth"], button:has-text("Login"), a:has-text("Iniciar"), a:has-text("Entrar")');
  if (await loginLink.first().isVisible({ timeout: 3000 }).catch(() => false)) {
    await loginLink.first().click();
  }
  await page.waitForTimeout(2000);

  // 3. We should now be on the Keycloak login form
  await page.waitForSelector('input[name="username"], input#username', { timeout: 10000 });
  await page.fill('input[name="username"], input#username', 'demo@nekazari.local');
  await page.fill('input[name="password"], input#password', 'Demo1234!');
  await page.click('input[type="submit"], button[type="submit"], #kc-login');

  // 4. Wait for redirect back to the app
  await page.waitForURL(/^(?!.*\/auth\/).*/, { timeout: 15000 });
  await page.waitForLoadState('domcontentloaded');

  // 5. Wait for the app shell to render (dashboard or entity list)
  await page.waitForSelector('body', { state: 'visible', timeout: 10000 });
  await page.waitForTimeout(2000);

  // 6. Save browser state (cookies, localStorage) for reuse
  await page.context().storageState({ path: AUTH_FILE });

  await browser.close();
}

export default globalSetup;
