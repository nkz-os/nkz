import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E configuration for Nekazari frontend.
 *
 * Run against docker-compose: E2E_BASE_URL=http://localhost:3000 pnpm test:e2e
 * Run against dev server:     pnpm test:e2e
 * Debug:                      pnpm test:e2e --debug
 * UI:                         pnpm test:e2e --ui
 */
export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global.setup.ts',
  fullyParallel: true,
  forbidOnly: !!process.env['CI'],
  retries: process.env['CI'] ? 2 : 0,
  workers: process.env['CI'] ? 1 : undefined,
  reporter: process.env['CI'] ? [['html'], ['list']] : 'html',

  use: {
    baseURL: process.env['E2E_BASE_URL'] || 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
      },
    },
  ],

  // Start local dev server when targeting localhost (no E2E_BASE_URL).
  // When running against docker-compose, the server is already running.
  ...(!process.env['E2E_BASE_URL']
    ? {
        webServer: {
          command: 'pnpm dev',
          url: 'http://localhost:3000',
          reuseExistingServer: !process.env['CI'],
          timeout: 120_000,
        },
      }
    : {}),
});
