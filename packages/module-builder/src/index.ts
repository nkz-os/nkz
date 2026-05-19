// =============================================================================
// @nekazari/module-builder — Vite preset for NKZ modules (Module Federation 2.0)
// =============================================================================
// Builds a module as a federated remote that the host loads at runtime via
// `registerRemotes` + `loadRemote('<id>/Module')`.
//
// Two source layouts:
//
// MODERN — `src/Module.tsx` exports `export default defineModule({...})`.
//   The preset exposes that file as `./Module`. After build, the host can
//   `loadRemote('<id>/Module')` and gets the validated definition.
//   The module id comes from `package.json#nkz.moduleId` (or the option).
//
//     // vite.config.ts
//     import { defineConfig } from 'vite';
//     import { nkzModulePreset } from '@nekazari/module-builder';
//     export default defineConfig(nkzModulePreset());
//
// LEGACY — `src/moduleEntry.ts` written by hand, still exposed as `./Module`.
//   It MUST `export default` a defineModule result. Side-effect-only legacy
//   entries that call `window.__NKZ__.register(...)` no longer work in 2.0.
//
//     export default defineConfig(nkzModulePreset({ moduleId: 'my-module' }));
//
// Output:
// - dist/remoteEntry.js          — federation entry (loaded by host)
// - dist/mf-manifest.json        — federation manifest (preload metadata)
// - dist/manifest.json           — NKZ data manifest (read by api-gateway CSP)
// - dist/assets/*.js             — chunks (lazy loaded by federation runtime)
// =============================================================================

import type { Plugin, UserConfig } from 'vite';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import react from '@vitejs/plugin-react';
import { federation } from '@module-federation/vite';
import { detectEntryStrategy } from './codegen.js';
import { emitManifest } from './manifestEmitter.js';

export { detectEntryStrategy, generateManifest } from './codegen.js';
export { emitManifest } from './manifestEmitter.js';

// =============================================================================
// Shared dependencies (Module Federation singleton contract)
// =============================================================================
// All modules in the platform must agree on a single instance of these libs.
// Singleton + requiredVersion forces the federation runtime to surface a
// version mismatch instead of silently loading two copies (which was the root
// cause of the AppInitializer crash under the old IIFE transport).

const NKZ_SHARED = {
    react: { singleton: true, requiredVersion: '^18.0.0' },
    'react-dom': { singleton: true, requiredVersion: '^18.0.0' },
    'react-router-dom': { singleton: true, requiredVersion: '^6.0.0' },
    'react-i18next': { singleton: true },
    i18next: { singleton: true },
    '@nekazari/sdk': { singleton: true },
    '@nekazari/module-kit': { singleton: true },
    '@nekazari/ui-kit': { singleton: true },
    '@nekazari/design-tokens': { singleton: true },
    '@nekazari/viewer-kit': { singleton: true },
} as const;

export interface NKZModulePresetOptions {
    /**
     * Module identifier. REQUIRED in legacy mode. In modern mode it is
     * derived from package.json#nkz.moduleId unless explicitly passed.
     */
    moduleId?: string;
    /** Entry point file (legacy mode only; default: 'src/moduleEntry.ts'). */
    entry?: string;
    /** Additional Vite config to merge. */
    viteConfig?: Partial<UserConfig>;
    /** Additional shared deps beyond the platform defaults. */
    additionalShared?: Record<string, unknown>;
    /** Project root (default: process.cwd()). */
    root?: string;
}

/**
 * Creates a Vite config that builds a Nekazari module as a federated remote.
 */
export function nkzModulePreset(options: NKZModulePresetOptions = {}): UserConfig {
    const {
        viteConfig = {},
        additionalShared = {},
        root = process.cwd(),
    } = options;

    const strategy = detectEntryStrategy(root);

    let entry: string;
    let moduleId: string;
    let manifestPlugin: Plugin | null = null;

    if (strategy === 'modern') {
        entry = './src/Module.tsx';
        moduleId = options.moduleId ?? readModuleIdFromPackage(root);
        // Emit dist/manifest.json after build for api-gateway CSP enforcement.
        manifestPlugin = {
            name: 'nkz-module-builder:manifest',
            apply: 'build',
            async closeBundle() {
                await emitManifest(root);
            },
        };
    } else {
        entry = options.entry ?? './src/moduleEntry.ts';
        if (!options.moduleId) {
            throw new Error('[module-builder] Legacy mode requires moduleId in nkzModulePreset({ moduleId: "..." })');
        }
        moduleId = options.moduleId;
    }

    // Federation `name` must be a valid JS identifier.
    const fedName = moduleId.replace(/[^a-zA-Z0-9_]/g, '_');

    // The federation plugin resolves each shared dep's package.json at build
    // time to verify the version. Modules that don't directly depend on a
    // shared package (e.g. modules that don't use react-router-dom) crash the
    // build with MODULE_NOT_FOUND. Filter the shared list to packages whose
    // package.json exists on disk under node_modules — bypassing exports map
    // strictness which would falsely reject @nekazari/* packages.
    //
    // EXCEPTION: packages in ALWAYS_SHARE are included unconditionally even
    // when not directly installed by the module. They are runtime singletons
    // used indirectly (e.g. react-i18next via @nekazari/sdk's useTranslation).
    // The host provides the concrete version; the remote just declares intent
    // to share so the federation runtime negotiates a single instance.
    const ALWAYS_SHARE = new Set(['react-i18next', 'i18next']);
    const sharedConfig: Record<string, unknown> = {};
    const alwaysShareExternals: string[] = [];
    for (const [pkg, opts] of Object.entries({ ...NKZ_SHARED, ...additionalShared })) {
        const installed = existsSync(join(root, 'node_modules', pkg, 'package.json'));
        if (ALWAYS_SHARE.has(pkg) || installed) {
            sharedConfig[pkg] = opts;
            if (!installed) {
                // Mark as Rollup external — the host provides it at runtime
                alwaysShareExternals.push(pkg);
            }
        }
        // else: not installed in this module — skip. The host still shares it
        // and other modules that need it can still import from the host.
    }

    const expectedPublicPath = `/modules/${moduleId}/`;

    // After build, assert dist/mf-manifest.json#metaData.publicPath matches
    // what the host expects. A wrong value (typically '/') means the
    // federation runtime will fetch remoteEntry.js from the host root and
    // 404 every module load. Catching it at build time prevents the regression
    // that hit the platform when an old module-builder slipped into a lockfile.
    const publicPathGuard: Plugin = {
        name: 'nkz-module-builder:public-path-guard',
        apply: 'build',
        closeBundle() {
            const manifestPath = join(root, 'dist/mf-manifest.json');
            if (!existsSync(manifestPath)) return;
            const raw = readFileSync(manifestPath, 'utf-8');
            const parsed = JSON.parse(raw) as { metaData?: { publicPath?: string } };
            const got = parsed.metaData?.publicPath;
            if (got !== expectedPublicPath) {
                throw new Error(
                    `[module-builder] mf-manifest.json#metaData.publicPath = ${JSON.stringify(got)}, ` +
                    `expected ${JSON.stringify(expectedPublicPath)}. ` +
                    `The host concatenates publicPath + chunk-name to fetch federation entries — a wrong ` +
                    `value will 404 every module load. Check that Vite \`base\` was not overridden to ` +
                    `a different value in viteConfig.`,
                );
            }
        },
    };

    const plugins: Plugin[] = [
        ...(react({ jsxRuntime: 'classic' }) as Plugin[]),
        ...(federation({
            name: fedName,
            filename: 'remoteEntry.js',
            exposes: {
                './Module': entry,
            },
            shared: sharedConfig as Record<string, never>,
            // Emit dist/mf-manifest.json so the host can preload chunks via
            // the federation runtime when registerRemotes() is called with a
            // manifest URL.
            manifest: true,
            // Federation's DTS extractor spawns a long-lived worker that hangs
            // the build. The host loads remotes through runtime APIs and treats
            // their exports as opaque, so cross-federation typings have no
            // consumer here. Disable.
            dts: false,
        }) as unknown as Plugin[]),
    ];
    if (manifestPlugin) plugins.push(manifestPlugin);
    plugins.push(publicPathGuard);

    const config: UserConfig = {
        plugins,

        // The module is served from `<host>/modules/<id>/` on MinIO. Vite's
        // `base` is what @module-federation/vite emits into the manifest's
        // `metaData.publicPath`; without it the runtime fetches chunks (incl.
        // `remoteEntry.js`) from the host root and 404s. Modules can override
        // by passing `viteConfig.base` if served from a different prefix.
        base: `/modules/${moduleId}/`,

        define: {
            'process.env.NODE_ENV': JSON.stringify('production'),
            '__NKZ_MODULE_ID__': JSON.stringify(moduleId),
        },

        build: {
            // Required for native top-level-await in the federation runtime.
            // Modern browsers (Chrome 89+, Safari 15+, Firefox 89+) support it.
            target: 'esnext',
            outDir: 'dist',
            emptyOutDir: true,
            sourcemap: true,
            minify: 'esbuild',
            // Federation manages its own chunk preloading; let it.
            modulePreload: false,
            copyPublicDir: false,
            rollupOptions: {
                // Skip index.html crawling. Modules ship a remote, not an app:
                // the federation plugin emits remoteEntry.js + chunks from the
                // exposes map, and the source entry is enough for tree-shaking.
                input: entry,
                // ALWAYS_SHARE packages not installed locally must be marked
                // external so Rollup does not try to resolve them. The host
                // provides them at runtime via the federation shared scope.
                external: alwaysShareExternals.length ? alwaysShareExternals : undefined,
            },
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
    if (viteConfig.base) {
        config.base = viteConfig.base;
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

export { NKZ_SHARED };
export default nkzModulePreset;
