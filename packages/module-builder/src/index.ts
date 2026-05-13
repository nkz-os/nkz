// =============================================================================
// @nekazari/module-builder — Vite Preset for NKZ Module IIFE Bundles
// =============================================================================
// Two modes:
//
// MODERN — src/Module.tsx exports `export default defineModule({...})`.
//   The preset auto-generates `node_modules/.nkz/moduleEntry.gen.ts` and
//   emits `dist/manifest.json`. The module id is read from
//   package.json#nkz.moduleId (or from the moduleId option).
//
//     // vite.config.ts
//     import { defineConfig } from 'vite';
//     import { nkzModulePreset } from '@nekazari/module-builder';
//     export default defineConfig(nkzModulePreset());
//
// LEGACY — src/moduleEntry.ts written by hand. The preset uses it as-is
//   and does NOT emit a manifest. This is the path the existing modules
//   use today. New modules should use modern mode.
//
//     export default defineConfig(nkzModulePreset({ moduleId: 'my-module' }));
//
// Either way the output is a single IIFE bundle at dist/nkz-module.js that:
// - Externalizes React, ReactDOM, @nekazari/sdk, @nekazari/ui-kit, etc.
// - Maps them to window globals provided by the host
// - Wraps everything in an IIFE that calls window.__NKZ__.register()
// =============================================================================

import type { Plugin, UserConfig } from 'vite';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import react from '@vitejs/plugin-react';
import {
    detectEntryStrategy,
    generateModuleEntry,
} from './codegen.js';

export { detectEntryStrategy, generateModuleEntry, generateManifest } from './codegen.js';

// =============================================================================
// External Dependencies — mapped to window globals provided by the host
// =============================================================================

const NKZ_EXTERNALS: Record<string, string> = {
    'react': 'React',
    'react-dom': 'ReactDOM',
    'react-dom/client': 'ReactDOM',
    'react-router-dom': 'ReactRouterDOM',
    '@nekazari/sdk': '__NKZ_SDK__',
    '@nekazari/ui-kit': '__NKZ_UI__',
    '@nekazari/design-tokens': '__NKZ_THEME__',
    '@nekazari/viewer-kit': '__NKZ_VIEWER__',
    '@nekazari/module-kit': '__NKZ_MODULE_KIT__',
};

export interface NKZModulePresetOptions {
    /**
     * Module identifier. REQUIRED in legacy mode. In modern mode it is
     * derived from package.json#nkz.moduleId unless explicitly passed.
     */
    moduleId?: string;
    /** Entry point file (legacy mode only; default: 'src/moduleEntry.ts'). */
    entry?: string;
    /** Output filename (default: 'nkz-module.js'). */
    outputFile?: string;
    /** Additional Vite config to merge. */
    viteConfig?: Partial<UserConfig>;
    /** Additional externals beyond the defaults. */
    additionalExternals?: Record<string, string>;
    /** Project root (default: process.cwd()). */
    root?: string;
}

/**
 * Creates a Vite config for building a Nekazari module as an IIFE bundle.
 */
export function nkzModulePreset(options: NKZModulePresetOptions = {}): UserConfig {
    const {
        outputFile = 'nkz-module.js',
        viteConfig = {},
        additionalExternals = {},
        root = process.cwd(),
    } = options;

    const strategy = detectEntryStrategy(root);

    let entry: string;
    let moduleId: string;

    if (strategy === 'modern') {
        entry = generateModuleEntry(root);
        moduleId = options.moduleId ?? readModuleIdFromPackage(root);
        // NOTE: dist/manifest.json emission is deferred to the `nkz build` CLI
        // (Fase A.2) where vite-node / jiti can safely evaluate the TSX source
        // to obtain the runtime ModuleDefinition. `generateManifest()` is still
        // exported from this package for the CLI to consume.
    } else {
        entry = options.entry ?? 'src/moduleEntry.ts';
        if (!options.moduleId) {
            throw new Error('[module-builder] Legacy mode requires moduleId in nkzModulePreset({ moduleId: "..." })');
        }
        moduleId = options.moduleId;
    }

    const allExternals = { ...NKZ_EXTERNALS, ...additionalExternals };
    const externalKeys = Object.keys(allExternals);
    const globals = { ...allExternals };

    const plugins: Plugin[] = [
        ...(react({ jsxRuntime: 'classic' }) as Plugin[]),
        {
            name: 'nkz-module-banner',
            generateBundle(_options, bundle) {
                for (const chunk of Object.values(bundle)) {
                    if (chunk.type === 'chunk' && chunk.isEntry) {
                        chunk.code = `/* NKZ Module: ${moduleId} | Built: ${new Date().toISOString()} */\n${chunk.code}`;
                    }
                }
            },
        },
    ];

    const config: UserConfig = {
        plugins,

        define: {
            'process.env.NODE_ENV': JSON.stringify('production'),
            '__NKZ_MODULE_ID__': JSON.stringify(moduleId),
        },

        build: {
            lib: {
                entry,
                name: `NKZModule_${moduleId.replace(/[^a-zA-Z0-9_]/g, '_')}`,
                formats: ['iife'],
                fileName: () => outputFile,
            },
            rollupOptions: {
                external: externalKeys,
                output: {
                    globals,
                    inlineDynamicImports: true,
                },
            },
            outDir: 'dist',
            emptyOutDir: true,
            sourcemap: true,
            minify: 'esbuild',
            copyPublicDir: false,
        },

        resolve: {
            alias: {
                '@': '/src',
            },
        },
    };

    if (viteConfig.plugins) {
        config.plugins = [...(config.plugins || []), ...viteConfig.plugins];
    }
    if (viteConfig.define) {
        config.define = { ...config.define, ...viteConfig.define };
    }
    if (viteConfig.resolve?.alias) {
        const existingAlias = (config.resolve?.alias as Record<string, string>) || {};
        const newAlias = viteConfig.resolve.alias as Record<string, string>;
        config.resolve = { ...config.resolve, alias: { ...existingAlias, ...newAlias } };
    }

    return config;
}

function readModuleIdFromPackage(root: string): string {
    const pkgPath = join(root, 'package.json');
    if (!existsSync(pkgPath)) {
        throw new Error(`[module-builder] package.json not found at ${pkgPath}`);
    }
    const pkg = JSON.parse(readFileSync(pkgPath, 'utf-8')) as {
        nkz?: { moduleId?: string };
        name?: string;
    };
    if (pkg.nkz?.moduleId) return pkg.nkz.moduleId;
    throw new Error(
        '[module-builder] Modern mode needs a moduleId. Add "nkz": { "moduleId": "your-id" } to package.json, or pass moduleId in nkzModulePreset({...}).',
    );
}

export { NKZ_EXTERNALS };
export default nkzModulePreset;
