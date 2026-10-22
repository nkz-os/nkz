/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import * as RadixTooltip from '@radix-ui/react-tooltip';

interface TooltipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  side?: 'top' | 'bottom' | 'left' | 'right';
  delayDuration?: number;
}

export function Tooltip({
  content,
  children,
  side = 'top',
  delayDuration = 500,
}: TooltipProps) {
  return (
    <RadixTooltip.Provider delayDuration={delayDuration}>
      <RadixTooltip.Root>
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            side={side}
            sideOffset={4}
            className="z-nkz-tooltip px-nkz-inline py-nkz-tight bg-nkz-text-primary text-nkz-text-on-accent text-nkz-xs rounded-nkz-sm shadow-nkz-md animate-in fade-in data-[state=closed]:animate-out data-[state=closed]:fade-out"
          >
            {content}
            <RadixTooltip.Arrow className="fill-nkz-text-primary" />
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  );
}
