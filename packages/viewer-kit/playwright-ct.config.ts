import { defineConfig, devices } from '@playwright/experimental-ct-react';

// DEVIATION from task-3-brief.md: the brief's snippet resolves aliases with
// `path.resolve(__dirname, ...)`, but this package is `"type": "module"`
// (package.json:20) — Playwright loads this config as real ESM, where
// `__dirname` is not defined (`ReferenceError: __dirname is not defined in ES
// module scope`, confirmed by running `test:ct:update`). ui-kit's own
// playwright-ct.config.ts (Task 2, same "type": "module" constraint) already
// works around this with `new URL(...).pathname` — mirrored here instead.
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
        alias: {
          // viewer-kit needs BOTH: SlotShell imports Panel from ui-kit.
          '@nekazari/design-tokens': new URL('../design-tokens/src/index.ts', import.meta.url).pathname,
          '@nekazari/ui-kit': new URL('../ui-kit/src/index.ts', import.meta.url).pathname,
        },
      },
    },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
