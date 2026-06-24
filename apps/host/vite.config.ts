import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import cesium from 'vite-plugin-cesium'
import { federation } from '@module-federation/vite'

const NKZ_SHARED = {
  react: { singleton: true, requiredVersion: '^18.0.0' },
  'react-dom': { singleton: true, requiredVersion: '^18.0.0' },
  'react/jsx-runtime': { singleton: true, requiredVersion: '^18.0.0', import: false },
  'react-router-dom': { singleton: true, requiredVersion: '^6.0.0' },
  '@tanstack/react-query': { singleton: true, requiredVersion: '^5.0.0' },
  'react-i18next': { singleton: true },
  i18next: { singleton: true },
  '@nekazari/sdk': { singleton: true },
  '@nekazari/module-kit': { singleton: true },
  '@nekazari/ui-kit': { singleton: true },
  '@nekazari/design-tokens': { singleton: true },
  '@nekazari/viewer-kit': { singleton: true },
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    cesium(),
    federation({
      name: 'nekazari_host',
      // Remotes are registered at runtime via @module-federation/enhanced/runtime
      // (see apps/host/src/context/ModuleContext.tsx) so the host doesn't need
      // to know module URLs at build time.
      remotes: {},
      shared: NKZ_SHARED,
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
    preserveSymlinks: true,
  },
  optimizeDeps: {
    include: [
      '@nekazari/sdk',
      '@nekazari/ui-kit',
      'js-sha256',
      '@kurkle/color',
      '@turf/helpers',
      '@turf/bbox',
      '@turf/intersect',
      '@turf/area',
      '@turf/boolean-contains',
      '@turf/boolean-disjoint',
      '@turf/boolean-point-in-polygon',
      '@turf/invariant',
    ]
  },
  ssr: {
    noExternal: ['@turf/*']
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/auth': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/ngsi-ld': {
        target: 'http://localhost:1026',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Required for Module Federation runtime (top-level await on remote loading)
    target: 'esnext',
    minify: 'esbuild',
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        entryFileNames: `assets/[name]-[hash].js`,
        chunkFileNames: `assets/[name]-[hash].js`,
        assetFileNames: `assets/[name]-[hash].[ext]`,
        // Manual vendor chunking removed: react/react-dom/react-router-dom are
        // managed by Module Federation as singleton shared deps. Manual chunks
        // would compete with that and risk loading two React copies.
      },
    },
  },
  define: {
    __APP_ENV__: JSON.stringify(process.env.NODE_ENV),
  },
})
