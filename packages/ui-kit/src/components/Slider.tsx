/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import * as RadixSlider from '@radix-ui/react-slider';
import clsx from 'clsx';

interface SliderProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  label?: string;
  unit?: string;
  disabled?: boolean;
  className?: string;
}

export function Slider({
  value,
  onChange,
  min = 0,
  max = 100,
  step = 1,
  label,
  unit,
  disabled,
  className,
}: SliderProps) {
  return (
    <div className={clsx('flex flex-col gap-nkz-tight', className)}>
      {(label || unit) && (
        <div className="flex justify-between text-nkz-xs">
          {label && <span className="text-nkz-text-secondary">{label}</span>}
          {unit && (
            <span className="text-nkz-text-muted">
              {value}
              {unit}
            </span>
          )}
        </div>
      )}
      <RadixSlider.Root
        value={[value]}
        onValueChange={([v]) => onChange(v)}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        className="relative flex items-center w-full h-5 cursor-pointer touch-none"
      >
        <RadixSlider.Track className="relative w-full h-1 rounded-full bg-nkz-border">
          <RadixSlider.Range className="absolute h-full rounded-full bg-nkz-accent-base" />
        </RadixSlider.Track>
        <RadixSlider.Thumb
          className={clsx(
            'block w-4 h-4 bg-white rounded-full shadow-nkz-sm border border-nkz-border-strong',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base',
            'hover:bg-nkz-surface-raised transition-colors duration-nkz-fast',
            'disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        />
      </RadixSlider.Root>
    </div>
  );
}
