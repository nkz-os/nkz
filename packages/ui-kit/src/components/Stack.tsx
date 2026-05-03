/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

type StackGap = 'tight' | 'inline' | 'stack' | 'section';

interface StackProps extends React.HTMLAttributes<HTMLDivElement> {
  gap?: StackGap;
  align?: 'start' | 'center' | 'end' | 'stretch';
  justify?: 'start' | 'center' | 'end' | 'between' | 'around';
}

const gapMap: Record<StackGap, string> = {
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
};

const justifyMap: Record<string, string> = {
  start: 'justify-start',
  center: 'justify-center',
  end: 'justify-end',
  between: 'justify-between',
  around: 'justify-around',
};

export function Stack({
  gap = 'stack',
  align,
  justify,
  className,
  ...props
}: StackProps) {
  return (
    <div
      className={clsx(
        'flex flex-col',
        gapMap[gap],
        align && alignMap[align],
        justify && justifyMap[justify],
        className
      )}
      {...props}
    />
  );
}
