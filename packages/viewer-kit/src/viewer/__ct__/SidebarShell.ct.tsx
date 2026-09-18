import { test, expect } from '@playwright/experimental-ct-react';
import { SidebarShell } from '../SidebarShell';

// ASSUMPTION: task-3-brief.md lists this file under "Files: Create" and under
// "Interfaces: Produces" but never gives its literal content (unlike
// SlotShell.ct.tsx, which is given verbatim in Step 2). Built by mirroring the
// established pattern (SlotShell.ct.tsx here, and ui-kit's __ct__ tests) against
// the real SidebarShellRoot props (SidebarShell.tsx:38-57) and compound
// sub-components (SidebarShell.tsx:285-289). `state`/`onStateChange` are a
// controlled pair — fixed to `expanded` with a no-op handler since this baseline
// captures static render, not interaction. Flagging for owner review.
const PROFILES = ['page', 'page-dark', 'viewer', 'viewer-light', 'field'] as const;

for (const profile of PROFILES) {
  test(`SidebarShell — perfil ${profile}`, async ({ mount }) => {
    const component = await mount(
      <div style={{ height: 400, position: 'relative' }}>
        <SidebarShell side="left" state="expanded" onStateChange={() => {}}>
          <SidebarShell.Pinned>Fijado</SidebarShell.Pinned>
          <SidebarShell.Groups>Grupos de modulos</SidebarShell.Groups>
          <SidebarShell.Hidden>Ocultos</SidebarShell.Hidden>
        </SidebarShell>
      </div>,
      { hooksConfig: { profile } },
    );
    await expect(component).toHaveScreenshot(`sidebarshell-${profile}.png`);
  });
}
