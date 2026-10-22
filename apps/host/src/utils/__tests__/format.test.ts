import { describe, it, expect } from 'vitest';
import { safeFixed } from '../format';

describe('safeFixed', () => {
  it('formats a valid number with default 1 decimal', () => {
    expect(safeFixed(3.5)).toBe('3.5');
  });

  it('formats with custom decimal places', () => {
    expect(safeFixed(3.14159, 4)).toBe('3.1416');
  });

  it('returns dash for null', () => {
    expect(safeFixed(null)).toBe('-');
  });

  it('returns dash for undefined', () => {
    expect(safeFixed(undefined)).toBe('-');
  });

  it('handles zero', () => {
    expect(safeFixed(0)).toBe('0.0');
  });

  it('handles negative numbers', () => {
    expect(safeFixed(-5.2)).toBe('-5.2');
  });

  it('handles integer values', () => {
    expect(safeFixed(10)).toBe('10.0');
  });

  it('handles very small numbers', () => {
    expect(safeFixed(0.001, 3)).toBe('0.001');
  });

  it('handles large numbers with default precision', () => {
    expect(safeFixed(1234567.89)).toBe('1234567.9');
  });
});
