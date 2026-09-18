import { test, expect } from '@playwright/experimental-ct-react';
import { Card } from '../Card';

const PROFILES = ['page', 'viewer', 'viewer-light', 'field'] as const;

for (const profile of PROFILES) {
  test(`Card — perfil ${profile}`, async ({ mount }) => {
    const component = await mount(
      <Card padding="md">Contenido de tarjeta</Card>,
      { hooksConfig: { profile } },
    );
    await expect(component).toHaveScreenshot(`card-${profile}.png`);
  });
}
