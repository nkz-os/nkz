import { test, expect } from '@playwright/experimental-ct-react';
import { MetricCard } from '../MetricCard';

const PROFILES = ['page', 'viewer', 'viewer-light', 'field'] as const;

for (const profile of PROFILES) {
  test(`MetricCard — tendencias — perfil ${profile}`, async ({ mount }) => {
    const component = await mount(
      <div style={{ display: 'flex', gap: 12 }}>
        <MetricCard label="Sube" value="12,4" trend={{ direction: 'up', value: '+3,1%' }} />
        <MetricCard label="Baja" value="8,1" trend={{ direction: 'down', value: '-1,8%' }} />
      </div>,
      { hooksConfig: { profile } },
    );
    await expect(component).toHaveScreenshot(`metriccard-${profile}.png`);
  });
}
