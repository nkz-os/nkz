/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 *
 * Ambient declarations for window globals the host application injects for
 * remote modules to read (see apps/host/src/context/KeycloakAuthContext.tsx
 * and apps/host/src/context/ViewerContext.tsx in the nkz monorepo). Both are
 * declared `unknown` on purpose: the host's real runtime shapes live inside
 * apps/host, which is an application, not a library the SDK can import
 * types from — and the host is free to evolve those shapes independently of
 * this package. Each consumer in @nekazari/sdk narrows defensively at its
 * own read site (see auth/useAuth.ts and viewer/useViewer.ts) rather than
 * trusting an unverifiable ambient shape.
 *
 * @nekazari/module-kit also reads `window.__nekazariAuthContext` (from its
 * own runtime/globals.ts) and declares the exact same `unknown` type for
 * that property, so the two packages' `Window` augmentations merge cleanly
 * for any consumer that imports both — TypeScript requires every merged
 * declaration of the same ambient member to share one identical type.
 */

import type { Context } from 'react';

declare global {
  interface Window {
    /** Auth bridge exposed by the host's KeycloakAuthContext provider. */
    __nekazariAuthContext?: unknown;
    /**
     * The host's ViewerContext React.Context INSTANCE (not its value),
     * exposed so remote modules can call useContext() on the host's own
     * context object directly instead of creating a second React context
     * across bundle boundaries. See viewer/useViewer.ts for the narrowed,
     * documented cast to ViewerContextValue.
     */
    __nekazariViewerContextInstance?: Context<unknown>;
  }
}

export {};
