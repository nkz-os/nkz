import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TimelineAxis } from '../TimelineAxis';
import { DAY_MS } from '../timeScale';

const range = { start: Date.UTC(2026, 3, 1), end: Date.UTC(2026, 3, 1) + 100 * DAY_MS };

describe('TimelineAxis', () => {
  it('moves the cursor to the clicked day, snapped to UTC midnight', () => {
    const onCursorChange = vi.fn();
    render(<TimelineAxis range={range} cursor={range.start} onCursorChange={onCursorChange} ariaLabel="axis" />);
    const track = screen.getByRole('slider', { name: 'axis' });
    track.getBoundingClientRect = () => ({ left: 0, width: 1000, top: 0, height: 20, right: 1000, bottom: 20, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.pointerDown(track, { clientX: 500, pointerId: 1 });
    expect(onCursorChange).toHaveBeenCalledWith(range.start + 50 * DAY_MS);
  });

  it('steps one day with arrow keys and clamps to the range', () => {
    const onCursorChange = vi.fn();
    render(<TimelineAxis range={range} cursor={range.end} onCursorChange={onCursorChange} ariaLabel="axis" />);
    const track = screen.getByRole('slider', { name: 'axis' });
    fireEvent.keyDown(track, { key: 'ArrowLeft' });
    expect(onCursorChange).toHaveBeenCalledTimes(1);
    expect(onCursorChange).toHaveBeenCalledWith(range.end - DAY_MS);
    fireEvent.keyDown(track, { key: 'ArrowRight' });
    expect(onCursorChange).toHaveBeenCalledTimes(1);
  });

  it("does not call onCursorChange when pointerDown lands on the cursor's own day", () => {
    const onCursorChange = vi.fn();
    render(<TimelineAxis range={range} cursor={range.start} onCursorChange={onCursorChange} ariaLabel="axis" />);
    const track = screen.getByRole('slider', { name: 'axis' });
    track.getBoundingClientRect = () => ({ left: 0, width: 1000, top: 0, height: 20, right: 1000, bottom: 20, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.pointerDown(track, { clientX: 0, pointerId: 1 });
    expect(onCursorChange).not.toHaveBeenCalled();
  });

  it('ignores pointerDown with non-primary button', () => {
    const onCursorChange = vi.fn();
    render(<TimelineAxis range={range} cursor={range.start} onCursorChange={onCursorChange} ariaLabel="axis" />);
    const track = screen.getByRole('slider', { name: 'axis' });
    track.getBoundingClientRect = () => ({ left: 0, width: 1000, top: 0, height: 20, right: 1000, bottom: 20, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.pointerDown(track, { clientX: 500, pointerId: 1, button: 2 });
    expect(onCursorChange).not.toHaveBeenCalled();
  });
});
