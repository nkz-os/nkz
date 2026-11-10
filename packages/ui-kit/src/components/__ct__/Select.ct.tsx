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

test('Select — arrows show which way the list can still scroll', async ({ mount, page }) => {
  await mount(<Select value="" onValueChange={() => {}} options={OPTIONS} placeholder="Pick" />);
  await page.getByRole('combobox').click();

  const up = page.locator('[data-direction="up"]');
  const down = page.locator('[data-direction="down"]');
  await expect(down).toBeVisible();
  await expect(up).toHaveCount(0);

  // Touch-like scroll: the focus stays on the first option. The list must stay at the bottom.
  const viewport = page.locator('[data-radix-select-viewport]');
  const bottom = await viewport.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
    return el.scrollHeight - el.clientHeight;
  });
  await expect(up).toBeVisible();
  await expect(down).toHaveCount(0);
  expect(await viewport.evaluate((el) => el.scrollTop)).toBeGreaterThanOrEqual(bottom - 1);
});

test('Select — a short list shows no arrows', async ({ mount, page }) => {
  await mount(<Select value="" onValueChange={() => {}} options={OPTIONS.slice(0, 3)} placeholder="Pick" />);
  await page.getByRole('combobox').click();
  await expect(page.getByRole('listbox')).toBeVisible();
  await expect(page.locator('[data-direction]')).toHaveCount(0);
});
