import { existsSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { build, type Plugin } from 'esbuild';
import { generateManifest } from './codegen.js';

interface EmitManifestOptions {
  /** Override the output directory (default: `<projectRoot>/dist`) */
  outDir?: string;
}

/**
 * esbuild plugin that stubs out Vite-specific virtual imports the manifest
 * phase will never actually execute. Worker bundles, raw assets, URL imports,
 * and CSS modules are not needed to derive the static defineModule({...})
 * payload — anything they reference lives inside lazy/handler callbacks that
 * don't run at module load time.
 */
const viteVirtualStubs: Plugin = {
  name: 'nkz-vite-virtual-stubs',
  setup(b) {
    b.onResolve({ filter: /\?(worker|sharedworker|raw|url|inline)(&|$)/ }, (args) => ({
      path: args.path,
      namespace: 'nkz-vite-stub',
    }));
    b.onLoad({ filter: /.*/, namespace: 'nkz-vite-stub' }, () => ({
      contents: 'export default function NKZManifestStub(){}',
      loader: 'js',
    }));
  },
};

/**
 * Bundle `src/Module.tsx` with esbuild (treating React and friends as external),
 * import the resulting file, and serialise the validated ModuleDefinition to
 * `<outDir>/manifest.json`. Returns the absolute path of the written file.
 *
 * esbuild is used instead of vite-node because vite-node's SSR runner fails to
 * resolve named exports across pnpm's file: link chains. The bundle phase only
 * needs to evaluate the static defineModule({...}) call — React hooks, lazy
 * imports, and worker imports never execute, so externalising the React stack
 * and stubbing Vite virtuals is safe.
 */
export async function emitManifest(
  projectRoot: string,
  opts: EmitManifestOptions = {},
): Promise<string> {
  const moduleFile = join(projectRoot, 'src/Module.tsx');
  if (!existsSync(moduleFile)) {
    throw new Error(
      `[module-builder] cannot emit manifest — src/Module.tsx not found in ${projectRoot}`,
    );
  }

  // Write the bundle inside the consumer's node_modules so that externalised
  // imports (react, react-dom, ...) resolve from the project's dependency
  // tree. A /tmp location would have no node_modules to resolve from.
  const tmpDir = join(projectRoot, 'node_modules/.nkz/manifest-emit');
  mkdirSync(tmpDir, { recursive: true });
  const bundleFile = join(tmpDir, `module-${Date.now()}.cjs`);

  try {
    await build({
      entryPoints: [moduleFile],
      bundle: true,
      platform: 'node',
      // CJS output is the only reliable shape: dependencies like react-draggable
      // do dynamic require('react') which the ESM-output __require shim cannot
      // satisfy. CJS lets Node resolve those natively from the project's tree.
      format: 'cjs',
      target: 'node18',
      outfile: bundleFile,
      absWorkingDir: projectRoot,
      external: [
        'react',
        'react-dom',
        'react-dom/client',
        'react-router',
        'react-router-dom',
        // NKZ runtime singletons (host-provided at runtime). The manifest
        // emit phase only evaluates the static `defineModule({...})` call,
        // which never invokes their named exports, so externalising prevents
        // esbuild from failing when a transitive import references an export
        // the installed version no longer ships (e.g. `Badge`).
        //
        // `@nekazari/module-kit` is deliberately NOT external — `defineModule`
        // itself is what we need to run, and the published package is ESM-only
        // so Node's CJS require cannot load it. Letting esbuild bundle it in
        // gives the CJS output a working defineModule callable.
        '@nekazari/sdk',
        '@nekazari/ui-kit',
        '@nekazari/design-tokens',
        '@nekazari/viewer-kit',
      ],
      loader: { '.tsx': 'tsx', '.ts': 'ts', '.css': 'empty', '.json': 'json' },
      logLevel: 'silent',
      write: true,
      mainFields: ['module', 'main'],
      conditions: ['import', 'node'],
      jsx: 'automatic',
      plugins: [viteVirtualStubs],
    });

    // Use createRequire so the bundle resolves its externals from the project,
    // and the CJS module.exports maps cleanly to a default export.
    const requireFromProject = createRequire(join(projectRoot, 'package.json'));
    const mod = requireFromProject(bundleFile) as { default?: unknown } | unknown;
    const def =
      mod && typeof mod === 'object' && 'default' in (mod as Record<string, unknown>)
        ? (mod as { default: unknown }).default
        : mod;
    if (!def || typeof def !== 'object') {
      throw new Error('[module-builder] src/Module.tsx must `export default defineModule({...})`');
    }

    const manifest = generateManifest(def as Parameters<typeof generateManifest>[0]);
    const outDir = opts.outDir ?? join(projectRoot, 'dist');
    mkdirSync(outDir, { recursive: true });
    const outFile = join(outDir, 'manifest.json');
    writeFileSync(outFile, JSON.stringify(manifest, null, 2), 'utf-8');
    return outFile;
  } finally {
    rmSync(bundleFile, { force: true });
  }
}
