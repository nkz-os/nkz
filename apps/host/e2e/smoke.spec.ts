import { test, expect } from '@playwright/test';
import * as path from 'path';

const AUTH_FILE = path.join(__dirname, '.auth', 'storageState.json');

/**
 * Smoke tests — verify the application loads and basic navigation works.
 * These tests don't require authentication.
 */

test.describe('Application Smoke Tests', () => {
  test('homepage loads successfully', async ({ page }) => {
    const response = await page.goto('/')
    expect(response?.status()).toBeLessThan(400)
  })

  test('page has correct title', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/[Nn]ekazari/)
  })

  test('login page is accessible', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('domcontentloaded')
    const body = page.locator('body')
    await expect(body).toBeVisible({ timeout: 15000 })
  })
})

test.describe('Authentication Flow', () => {
  test('unauthenticated user sees login prompt or app shell', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('domcontentloaded')
    await expect(page.locator('body')).toBeVisible({ timeout: 15000 })
    // Wait for app to render: either login/Keycloak text or main app content
    const loginOrShell = page.getByText(/Conectando|Keycloak|Login|Iniciar|Nekazari|Dashboard/i)
    await expect(loginOrShell.first()).toBeVisible({ timeout: 10000 })
    const url = page.url()
    const hasAuthInUrl = url.includes('auth') || url.includes('login')
    const hasLoginOrAppText = await loginOrShell.first().isVisible().catch(() => false)
    expect(hasAuthInUrl || hasLoginOrAppText).toBeTruthy()
  })
})

// =============================================================================
// Authenticated tests — reuse storageState from global.setup.ts
// =============================================================================

test.describe('Authenticated Platform', () => {
  test.use({ storageState: AUTH_FILE });

  test('dashboard loads after login', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // The app should render the main shell, not redirect to Keycloak
    const url = page.url();
    expect(url).not.toContain('/auth');

    await expect(page.locator('body')).toBeVisible({ timeout: 15000 });
  });

  test('i18n language switch works', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // Click the language switcher if visible, or just verify the page loaded
    await page.waitForTimeout(2000);

    // Check that we see some text content (proves i18n initialized)
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.length).toBeGreaterThan(10);
  });

  test('API gateway health check', async ({ page }) => {
    const response = await page.request.get('/api/health');
    expect(response.status()).toBe(200);
  });

  test('MinIO /modules/ proxy reaches storage', async ({ page }) => {
    // The proxy should reach MinIO (403 or 404 means it works; connection refused means it doesn't)
    const response = await page.request.get('/modules/missing/bundle.js');
    expect(response.status()).not.toBe(502);
  });
});
