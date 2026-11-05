import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (key: string) => key }) }));

import { PlacementModeSelector } from '../PlacementModeSelector';

// The wizard is a narrow side panel: cards stack in one column whatever the
// screen width, and each card grows with its text instead of the button's
// fixed height (which made the texts overlap).
describe('PlacementModeSelector layout', () => {
  it('stacks the mode cards in one column and lets each grow with its text', () => {
    const { container } = render(<PlacementModeSelector mode="single" onChange={() => {}} entityType="AgriPlant" />);
    const grid = container.firstElementChild as HTMLElement;
    expect(grid.className).not.toMatch(/(^|\s)\w+:grid-cols-/);
    const cards = screen.getAllByRole('button');
    expect(cards).toHaveLength(4);
    for (const card of cards) {
      expect(card.className.split(/\s+/)).toEqual(expect.arrayContaining(['h-auto', 'w-full', 'justify-start']));
      expect(card.className.split(/\s+/)).not.toContain('h-9');
    }
  });
});
