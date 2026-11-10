import { test, expect } from '@playwright/experimental-ct-react';
import { Select } from '../Select';

const OPTIONS = Array.from({ length: 60 }, (_, i) => ({ value: `opt-${i}`, label: `Option ${i}` }));

test.use({ viewport: { width: 400, height: 400 } });

test('Select — a long list stays inside the viewport and its last option is reachable', async ({ mount, page }) => {
  const picked: string[] = [];
  await mount(
    <Select value="" onValueChange={(v) => picked.push(v)} options={OPTIONS} placeholder="Pick" />,
  );

  await page.getByRole('combobox').click();
  const listbox = page.getByRole('listbox');
  await expect(listbox).toBeVisible();

  const box = await listbox.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.y + box!.height).toBeLessThanOrEqual(400);

  const last = page.getByRole('option', { name: 'Option 59' });
  await last.scrollIntoViewIfNeeded();
  await last.click();
  expect(picked).toEqual(['opt-59']);
});
