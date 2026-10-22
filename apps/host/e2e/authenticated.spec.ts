import { test, expect } from '@playwright/test';
import { AUTH_STATE_PATH } from './global.setup';

/**
 * Authenticated smoke test — proves the storageState persisted by
 * global.setup.ts (real Keycloak login + BFF session) actually restores a
 * logged-in session, instead of re-driving the login UI per test.
 */
test.use({ storageState: AUTH_STATE_PATH });

test.describe('Authenticated session', () => {
  test('logged-in user lands on the dashboard with a visible logout control', async ({ page }) => {
    await page.goto('/dashboard');

    // Logout button only renders once KeycloakAuthContext resolves an
    // authenticated user — its presence proves the restored session took,
    // not just that we're on the right route.
    await expect(page.getByTitle(/Log Out|Cerrar Sesión/i)).toBeVisible({ timeout: 15_000 });

    expect(page.url()).not.toContain('/login');
  });
});
