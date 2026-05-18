/**
 * End-to-end module deploy test — uploads the connectivity module dist
 * via POST /api/modules/<id>/dist, verifies the DB row, then reloads
 * the frontend and checks that the module loads without Federation errors.
 */
import { test, expect } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

const AUTH_FILE = path.join(__dirname, '.auth', 'storageState.json');
const DIST_DIR = path.join(__dirname, 'fixtures', 'connectivity-dist');
const MODULE_ID = 'connectivity';

test.describe('Module Deploy & Load', () => {
  test.use({ storageState: AUTH_FILE });

  test('deploy connectivity module via API', async ({ page }) => {
    // 1. Gather dist/ files
    const entries = fs.readdirSync(DIST_DIR, { recursive: true, withFileTypes: true });
    const multipart: Array<{ name: string; body: Buffer; filename: string }> = [];

    for (const entry of entries) {
      if (!entry.isFile()) continue;
      const relPath = path.relative(DIST_DIR, path.join(entry.parentPath ?? '', entry.name));
      multipart.push({
        name: 'file',
        body: fs.readFileSync(path.join(entry.parentPath ?? '', entry.name)),
        filename: relPath,
      });
    }

    expect(multipart.length).toBeGreaterThan(0);

    // 2. Upload via POST /api/modules/<id>/dist
    const deployRes = await page.request.post(
      `/api/modules/${MODULE_ID}/dist`,
      { multipart },
    );

    // 201 = created; 200 = already exists from a previous run (idempotent)
    expect([200, 201]).toContain(deployRes.status());

    // 3. Verify the module is registered in marketplace_modules
    const modulesRes = await page.request.get('/api/modules/marketplace');
    expect(modulesRes.status()).toBe(200);
    const modules = await modulesRes.json();
    const conn = modules.find((m: any) => m.id === MODULE_ID);
    expect(conn).toBeTruthy();
    expect(conn.remoteEntry).toContain(MODULE_ID);
  });

  test('module loads in frontend without errors', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);

    // The connectivity module has navigation (priority 50, section 'modules').
    // Look for it in the sidebar or module list.
    const moduleLink = page.locator(
      `a[href*="${MODULE_ID}"], [data-module-id="${MODULE_ID}"]`,
    );
    const visible = await moduleLink.first().isVisible().catch(() => false);

    if (visible) {
      await moduleLink.first().click();
      await page.waitForLoadState('domcontentloaded');
    }

    // Allow time for Federation to resolve the remote
    await page.waitForTimeout(3000);

    // Check for Federation errors
    const fedErrors = consoleErrors.filter(
      (e) =>
        e.includes('Failed to fetch dynamically imported module') ||
        e.includes('Module Federation') ||
        e.includes('loadRemote'),
    );

    expect(fedErrors).toHaveLength(0);
  });

  test('modules-manifest health check', async ({ page }) => {
    // Verify the host can reach the module's mf-manifest.json through nginx
    const res = await page.request.get(
      `/modules/${MODULE_ID}/mf-manifest.json`,
    );
    expect(res.status()).toBe(200);
    const manifest = await res.json();
    expect(manifest).toHaveProperty('name');
    expect(manifest).toHaveProperty('exposes');
  });
});
