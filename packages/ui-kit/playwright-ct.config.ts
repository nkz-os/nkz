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
  use: {
    ctViteConfig: {
      resolve: {
        alias: { '@nekazari/design-tokens': new URL('../design-tokens/src/index.ts', import.meta.url).pathname },
      },
    },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
