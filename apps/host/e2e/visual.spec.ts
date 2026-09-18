import { test, expect, type Page } from '@playwright/test';
import { AUTH_STATE_PATH } from './global.setup';

/**
 * Page-level visual regression baseline for the host app.
 *
 * Complements the component-level nets in ui-kit/viewer-kit with full-page
 * screenshots of the key authenticated screens, in both themes. This exists
 * to catch regressions from the upcoming theme-engine rework, which touches
 * every page — a component-level net alone doesn't cover page composition,
 * layout, or cross-component theme interaction.
 *
 * Determinism sources neutralised (see report for the full rationale):
 *   1. Theme forced via localStorage['nekazari-theme'] in an init script,
 *      set before the app boots — bypasses 'system' theme resolution
 *      (prefers-color-scheme) entirely, so it doesn't depend on whatever
 *      color scheme the OS/browser/CI runner happens to report.
 *   2. Clock frozen (page.clock) before navigation — TenantInfoWidget runs
 *      `setInterval(() => setTime(new Date()), 1000)` unconditionally on
 *      /dashboard; without freezing, the displayed clock (and any other
 *      `new Date()` read) ticks between runs and the screenshot never
 *      matches itself twice.
 *   3. CSS animations/transitions collapsed to ~0 duration via an injected
 *      stylesheet — spinners (`animate-spin`), pulsing "live" indicators
 *      (`animate-pulse`) and hover/focus transitions otherwise land on a
 *      different frame every capture.
 *   4. The Cesium WebGL globe canvas (unified viewer + entity wizard, which
 *      renders on top of it) hidden via `visibility: hidden` — it renders
 *      continuously (tile loads, easing, atmosphere shading), so raw canvas
 *      pixels are never stable. Playwright's `mask` option was tried first
 *      and rejected: it paints an opaque box at the element's full-page
 *      bounding rect regardless of stacking order, which blotted out the
 *      wizard modal and viewer chrome rendered on top of the canvas.
 *   5. Chromium launched with --disable-gpu (playwright.config.ts) plus a
 *      10px crop on the viewer/entity-wizard capture edge — even in
 *      software rendering, those two screens (full-bleed near-black
 *      background flush against the viewport edge) showed a handful of
 *      1-LSB pixel drifts at the four corners/edges between runs, a
 *      Chromium compositor antialiasing artifact, not app content. Cropping
 *      it out is cheaper and more honest than loosening the comparison.
 *
 * Known local-stack gaps (not app bugs): the dashboard's weather/sensor/
 * robot widgets and the module-federation remote slot 404/502/504 locally.
 * Those error states are captured as-is — they are deterministic (same
 * request, same failure, every run) so they're valid baseline content; see
 * the report for exactly which regions they occupy.
 */

test.use({ storageState: AUTH_STATE_PATH });

type ThemeName = 'light' | 'dark';
const THEMES: ThemeName[] = ['light', 'dark'];

const FROZEN_TIME = new Date('2026-01-01T09:00:00.000Z');

// Viewer/entity-wizard are fixed-height (non-scrolling) command-center
// layouts, exactly viewport-sized — crop a margin off all four edges to
// dodge the corner/edge antialiasing drift (see note 5 above).
const VIEWPORT = { width: 1280, height: 720 };
const EDGE_CROP = 10;
const CROPPED_CLIP = {
  x: EDGE_CROP,
  y: EDGE_CROP,
  width: VIEWPORT.width - EDGE_CROP * 2,
  height: VIEWPORT.height - EDGE_CROP * 2,
};

async function prepare(page: Page, theme: ThemeName, opts: { hideCanvas?: boolean } = {}) {
  await page.clock.install({ time: FROZEN_TIME });

  await page.addInitScript((t) => {
    window.localStorage.setItem('nekazari-theme', t);
  }, theme);

  await page.addInitScript((hideCanvas) => {
    const style = document.createElement('style');
    style.setAttribute('data-visual-net', 'freeze-animations');
    style.textContent = `
      *, *::before, *::after {
        animation-duration: 0.001ms !important;
        animation-delay: -0.001ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.001ms !important;
        transition-delay: 0s !important;
        scroll-behavior: auto !important;
        caret-color: transparent !important;
      }
      ${hideCanvas ? `
      /* Cesium's WebGL globe renders continuously (tile loads, easing,
         atmosphere shading) — CSS animation-freeze doesn't touch canvas
         pixel content, so no two captures are ever pixel-identical.
         Hiding it (layout box preserved, so surrounding chrome doesn't
         reflow) neutralises it at the source. Note: Playwright's own
         'mask' option was tried first and rejected — it paints an opaque
         box at the element's full-page bounding rect regardless of
         stacking order, which blots out the wizard modal and viewer
         chrome rendered *on top* of the (full-viewport) canvas. */
      canvas { visibility: hidden !important; }
      ` : ''}
    `;
    document.addEventListener('DOMContentLoaded', () => document.head.appendChild(style));
  }, opts.hideCanvas ?? false);
}

// CesiumMap creates a real WebGL context on mount even with the canvas
// hidden via CSS (visibility doesn't stop it rendering). Its cleanup effect
// calls viewer.destroy() on unmount, but Playwright's own context teardown
// between tests doesn't wait for that React unmount to run — leaving a live
// GL context that intermittently breaks the *next* test's CDP screenshot
// capture with "Protocol error (Page.captureScreenshot): Unable to capture
// screenshot" (reproduced consistently: entity-wizard → admin, worker
// reused). Navigating away first forces the unmount/destroy synchronously
// before the test ends, so the next test in the worker starts clean.
async function teardownCesium(page: Page) {
  await page.goto('about:blank');
}

async function gotoAndSettle(page: Page, path: string) {
  await page.goto(path, { waitUntil: 'networkidle' });
  await page.waitForLoadState('networkidle');
  // Let the last batch of React state updates (post-networkidle) flush
  // before we screenshot — networkidle only guarantees the network is
  // quiet, not that the resulting render has committed.
  await page.waitForTimeout(500);
}

test.describe('Page visual baseline', () => {
  for (const theme of THEMES) {
    test.describe(`theme=${theme}`, () => {
      test(`dashboard (${theme})`, async ({ page }) => {
        await prepare(page, theme);
        await gotoAndSettle(page, '/dashboard');
        await expect(page).toHaveScreenshot(`dashboard-${theme}.png`, { fullPage: true });
      });

      test(`unified viewer (${theme})`, async ({ page }) => {
        // Cesium init + explicit teardown navigation (see teardownCesium)
        // routinely runs past the 30s default under load.
        test.setTimeout(60_000);
        await prepare(page, theme, { hideCanvas: true });
        await gotoAndSettle(page, '/entities');
        await page.locator('canvas').first().waitFor({ state: 'attached', timeout: 15_000 });
        await expect(page).toHaveScreenshot(`viewer-${theme}.png`, { clip: CROPPED_CLIP });
        await teardownCesium(page);
      });

      test(`entity wizard (${theme})`, async ({ page }) => {
        test.setTimeout(60_000);
        await prepare(page, theme, { hideCanvas: true });
        await gotoAndSettle(page, '/entities');
        await page.getByRole('button', { name: 'Add' }).click();
        // Wizard modal's own heading (hardcoded, not the pre-existing
        // "Assets" panel heading behind it) — proves the modal has mounted.
        await expect(page.getByText('Crear Nueva Entidad')).toBeVisible({ timeout: 10_000 });
        await page.waitForTimeout(300);
        await expect(page).toHaveScreenshot(`entity-wizard-${theme}.png`, { clip: CROPPED_CLIP });
        await teardownCesium(page);
      });

      test(`admin (${theme})`, async ({ page }) => {
        await prepare(page, theme);
        await gotoAndSettle(page, '/admin/management');
        await expect(page).toHaveScreenshot(`admin-${theme}.png`, { fullPage: true });
      });

      test(`settings (${theme})`, async ({ page }) => {
        await prepare(page, theme);
        await gotoAndSettle(page, '/settings');
        await expect(page).toHaveScreenshot(`settings-${theme}.png`, { fullPage: true });
      });
    });
  }
});
