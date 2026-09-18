import { test, expect } from '@playwright/experimental-ct-react';
import { ProgressBar } from '../ProgressBar';

const PROFILES = ['page', 'page-dark', 'viewer', 'viewer-light', 'field'] as const;
// Firma real (ui-kit/src/components/ProgressBar.tsx:11-17): la prop es `intent`,
// NO `variant`. Intents: 'default' | 'positive' | 'warning' | 'negative' — no hay 'info'.
// `default` ya usa bg-nkz-accent-base (tokenizado); los otros 3 están cableados.
const INTENTS = ['default', 'positive', 'warning', 'negative'] as const;

for (const profile of PROFILES) {
  test(`ProgressBar — perfil ${profile}`, async ({ mount }) => {
    const component = await mount(
      <div style={{ display: 'grid', gap: 8, width: 240 }}>
        {INTENTS.map((intent) => (
          <ProgressBar key={intent} value={60} intent={intent} />
        ))}
      </div>,
      { hooksConfig: { profile } },
    );
    await expect(component).toHaveScreenshot(`progressbar-${profile}.png`);
  });
}
