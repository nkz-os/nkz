import { test, expect } from '@playwright/experimental-ct-react';
import { SlotShell, SlotShellCompact } from '../SlotShell';

const PROFILES = ['page', 'page-dark', 'viewer', 'viewer-light', 'field'] as const;

for (const profile of PROFILES) {
  test(`SlotShell — perfil ${profile}`, async ({ mount }) => {
    const component = await mount(
      <div style={{ width: 360 }}>
        <SlotShell moduleId="ct-fixture" title="Panel de ejemplo">
          Contenido del widget
        </SlotShell>
      </div>,
      { hooksConfig: { profile } },
    );
    await expect(component).toHaveScreenshot(`slotshell-${profile}.png`);
  });

  test(`SlotShellCompact — perfil ${profile}`, async ({ mount }) => {
    const component = await mount(
      <div style={{ width: 360 }}>
        <SlotShellCompact moduleId="ct-fixture">Contenido</SlotShellCompact>
      </div>,
      { hooksConfig: { profile } },
    );
    await expect(component).toHaveScreenshot(`slotshell-compact-${profile}.png`);
  });
}
