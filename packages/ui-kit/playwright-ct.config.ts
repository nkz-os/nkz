import { defineConfig, devices } from '@playwright/experimental-ct-react';

export default defineConfig({
  testDir: './src',
  testMatch: '**/*.ct.tsx',
  snapshotDir: './__snapshots__',
  timeout: 20_000,
  // fullyParallel is safe for comparison (test:ct). Capture (test:ct:update)
  // has a build/render race that can bake stale CSS into a screenshot even
  // when the on-disk CSS is correct, so it serialises via --workers=1 in the
  // test:ct:update script instead of being restricted here.
  fullyParallel: true,
  forbidOnly: !!process.env['CI'],
  retries: 0,
  reporter: process.env['CI'] ? [['list']] : 'html',
  // Strict pixel comparison: Playwright's default threshold is tolerant enough
  // that a real token/colour change can fall below it and pass against a
  // stale snapshot (confirmed on Card: border colour drifted to a hardcoded
  // gray-200 while 16/16 screenshot comparisons stayed green). A baseline
  // whose whole purpose is catching colour/token regressions must compare
  // exactly, or it isn't doing its job.
  expect: {
    toHaveScreenshot: { threshold: 0, maxDiffPixels: 0 },
  },
  use: {
    ctViteConfig: {
      resolve: {
        alias: { '@nekazari/design-tokens': new URL('../design-tokens/src/index.ts', import.meta.url).pathname },
      },
    },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
