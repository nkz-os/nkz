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

  // Strict pixel comparison for toHaveScreenshot — matches ui-kit/viewer-kit's
  // component-level visual nets (see packages/*/playwright-ct.config.ts). The
  // default threshold is tolerant enough to hide real regressions.
  expect: {
    toHaveScreenshot: { threshold: 0, maxDiffPixels: 0 },
  },

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Force software rasterization. Hardware GPU compositing was
        // producing a handful of off-by-one-LSB pixels at the four
        // viewport corners on full-page screenshots — invisible to the
        // eye but enough to break a threshold:0/maxDiffPixels:0 visual
        // baseline (see apps/host/e2e/visual.spec.ts). Software rendering
        // is bit-exact across runs.
        launchOptions: { args: ['--disable-gpu', '--disable-dev-shm-usage'] },
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
