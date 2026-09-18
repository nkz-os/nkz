import { test, expect } from '@playwright/experimental-ct-react';
import { Badge } from '../Badge';

const PROFILES = ['page', 'page-dark', 'viewer', 'viewer-light', 'field'] as const;
const INTENTS = ['default', 'positive', 'warning', 'negative', 'info'] as const;

for (const profile of PROFILES) {
  test(`Badge — todos los intents — perfil ${profile}`, async ({ mount }) => {
    const component = await mount(
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {INTENTS.map((intent) => (
          <Badge key={intent} intent={intent}>{intent}</Badge>
        ))}
      </div>,
      { hooksConfig: { profile } },
    );
    await expect(component).toHaveScreenshot(`badge-${profile}.png`);
  });
}
