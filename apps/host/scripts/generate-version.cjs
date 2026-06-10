#!/usr/bin/env node
/**
 * Generate version.json with build metadata.
 * Run before `vite build` to stamp the dist with build info.
 *
 * Usage:
 *   node scripts/generate-version.js
 *   pnpm run build  (calls this automatically via prebuild script)
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const now = new Date().toISOString();
let commitHash = 'unknown';

try {
  commitHash = execSync('git rev-parse --short HEAD', { encoding: 'utf-8' }).trim();
} catch {
  // not a git repo or git not available — use 'unknown'
}

const versionData = {
  version: commitHash,
  buildTime: now,
};

const outPath = path.resolve(__dirname, '..', 'public', 'version.json');
fs.writeFileSync(outPath, JSON.stringify(versionData, null, 2) + '\n', 'utf-8');

console.log(`[version] Generated ${outPath} — ${commitHash} @ ${now}`);
