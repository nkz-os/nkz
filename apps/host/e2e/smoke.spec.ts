import { test, expect } from '@playwright/test';

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
    await expect(body).toBeVisible({ timeout: 30000 })
  })
})

test.describe('Authentication Flow', () => {
  test('unauthenticated user sees login prompt or app shell', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('domcontentloaded')
    await expect(page.locator('body')).toBeVisible({ timeout: 30000 })
    // Wait for app to render: either login/Keycloak text or main app content
    const loginOrShell = page.getByText(/Conectando|Keycloak|Login|Iniciar|Nekazari|Dashboard/i)
    await expect(loginOrShell.first()).toBeVisible({ timeout: 20000 })
    const url = page.url()
    const hasAuthInUrl = url.includes('auth') || url.includes('login')
    const hasLoginOrAppText = await loginOrShell.first().isVisible().catch(() => false)
    expect(hasAuthInUrl || hasLoginOrAppText).toBeTruthy()
  })
})

// =============================================================================
// Infrastructure tests (no auth needed)
// =============================================================================

test.describe('Infrastructure Health', () => {
  test('frontend health endpoint', async ({ page }) => {
    const r = await page.request.get('/health');
    expect(r.status()).toBe(200);
  });

  test('API gateway reachable through nginx', async ({ page }) => {
    // /api/health returns 4xx (no auth) but proves routing works
    const r = await page.request.get('/api/health');
    expect(r.status()).not.toBe(502); // 502 = bad gateway (upstream down)
  });

  test('MinIO modules proxy reachable', async ({ page }) => {
    const r = await page.request.get('/modules/missing/bundle.js');
    // 403/404 means MinIO is reachable; 502 means it's not
    expect(r.status()).not.toBe(502);
  });
});
