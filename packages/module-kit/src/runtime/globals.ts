/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 *
 * Ambient declarations for window globals injected into each MF2 remote's
 * runtime scope, or exposed by the host application for modules to read.
 */

import type { PlatformEvents } from './events';

declare global {
  interface Window {
    /**
     * Legacy IIFE module registry + platform event bus. Modules were
     * migrated to Module Federation 2.0 (canonical pattern since
     * 2026-05-18) and the host no longer sets `window.__NKZ__` itself, but
     * `initPlatformEvents()` still reads `.events` defensively in case some
     * other layer wires it — falling back to a local, in-memory event bus
     * otherwise.
     */
    __NKZ__?: {
      events?: PlatformEvents;
    };
    /**
     * Bridge so multiple bundled copies of `@nekazari/sdk` (one per MF2
     * remote) can share a single i18n instance owned by the host.
     */
    __NKZ_SDK__?: {
      i18n?: {
        t?: (key: string, vars?: Record<string, unknown>) => string;
        changeLanguage?: (lang: string) => void;
      };
    };
    /** Module id injected into each MF2 remote's runtime scope. */
    __NKZ_MODULE_ID__?: string;
    /** Module's own backend base path injected into each MF2 remote's runtime scope. */
    __NKZ_MODULE_BASE_PATH__?: string;
    /**
     * Auth bridge exposed by the host's KeycloakAuthContext provider.
     * Declared `unknown` — same as `@nekazari/sdk`'s ambient declaration for
     * this property (see `@nekazari/sdk/src/types/global.ts`) so the two
     * packages' `Window` augmentations merge cleanly for any consumer that
     * imports both (TypeScript requires merged ambient members to share one
     * identical type). Narrowed defensively at each read site in this
     * package — see runtime/NKZProvider.tsx and hooks/useAPI.ts.
     */
    __nekazariAuthContext?: unknown;
  }
}

export {};
