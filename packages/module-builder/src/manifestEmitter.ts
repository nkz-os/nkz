import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createServer } from 'vite';
import { ViteNodeRunner } from 'vite-node/client';
import { ViteNodeServer } from 'vite-node/server';
import { installSourcemapsSupport } from 'vite-node/source-map';
import { generateManifest } from './codegen.js';

interface EmitManifestOptions {
  /** Override the output directory (default: `<projectRoot>/dist`) */
  outDir?: string;
}

/**
 * Evaluate `src/Module.tsx` via vite-node, derive the manifest, and write it
 * to `<outDir>/manifest.json`. Returns the absolute path of the written file.
 *
 * Used by `nkzModulePreset`'s `closeBundle` hook to emit a runtime-safe
 * manifest the host can read from MinIO.
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

  const server = await createServer({
    root: projectRoot,
    appType: 'custom',
    server: { middlewareMode: true, hmr: false },
    logLevel: 'error',
    ssr: { noExternal: [/@nekazari\/.+/] },
    optimizeDeps: { noDiscovery: true, include: [] },
  });

  try {
    const nodeServer = new ViteNodeServer(server);
    installSourcemapsSupport({ getSourceMap: (s) => nodeServer.getSourceMap(s) });
    const runner = new ViteNodeRunner({
      root: server.config.root,
      base: server.config.base,
      fetchModule: (id) => nodeServer.fetchModule(id),
      resolveId: (id, importer) => nodeServer.resolveId(id, importer),
    });

    const mod = (await runner.executeFile(moduleFile)) as { default?: unknown };
    const def = mod.default;
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
    await server.close();
  }
}
