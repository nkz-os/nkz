/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

type InlineGap = 'tight' | 'inline' | 'stack' | 'section';

interface InlineProps extends React.HTMLAttributes<HTMLDivElement> {
  gap?: InlineGap;
  align?: 'start' | 'center' | 'end' | 'stretch' | 'baseline';
  justify?: 'start' | 'center' | 'end' | 'between' | 'around';
  wrap?: boolean;
}

const gapMap: Record<InlineGap, string> = {
  tight: 'gap-nkz-tight',
  inline: 'gap-nkz-inline',
  stack: 'gap-nkz-stack',
  section: 'gap-nkz-section',
};

const alignMap: Record<string, string> = {
  start: 'items-start',
  center: 'items-center',
  end: 'items-end',
  stretch: 'items-stretch',
  baseline: 'items-baseline',
};

const justifyMap: Record<string, string> = {
  start: 'justify-start',
  center: 'justify-center',
  end: 'justify-end',
  between: 'justify-between',
  around: 'justify-around',
};

export function Inline({
  gap = 'inline',
  align,
  justify,
  wrap = true,
  className,
  ...props
}: InlineProps) {
  return (
    <div
      className={clsx(
        'flex flex-row',
        wrap && 'flex-wrap',
        gapMap[gap],
        align && alignMap[align],
        justify && justifyMap[justify],
        className
      )}
      {...props}
    />
  );
}
