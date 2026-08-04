/**
 * Global type declarations for the Nekazari host runtime.
 * These globals are injected by the host before module bundles execute.
 */
import type { ModuleViewerSlots } from '@nekazari/sdk';

declare global {
  interface Window {
    /**
     * Host module registry. Under Module Federation 2.0 the host calls
     * `.register()` internally (via `toNKZRegistration()` in `@nekazari/module-kit`,
     * fed by your `defineModule()` export in moduleEntry.ts) — module authors
     * must NOT call this directly.
     */
    __NKZ__: {
      register(module: {
        id: string;
        viewerSlots: ModuleViewerSlots;
        version?: string;
        main?: import('react').ComponentType;
      }): void;
    };
    /** @nekazari/sdk exposed by the host */
    __NKZ_SDK__: typeof import('@nekazari/sdk');
    /** @nekazari/ui-kit exposed by the host */
    __NKZ_UI__: typeof import('@nekazari/ui-kit');
    /** React 18 exposed by the host */
    React: typeof import('react');
    ReactDOM: typeof import('react-dom');
    ReactRouterDOM: typeof import('react-router-dom');
    /** CesiumJS — available in the map viewer context */
    Cesium?: unknown;
  }
}

export {};
