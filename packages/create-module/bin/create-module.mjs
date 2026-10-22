#!/usr/bin/env node
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// ---------------------------------------------------------------------------
// Minimal prompt helpers (no dependency needed for simple Q&A)
// ---------------------------------------------------------------------------

function ask(question, defaultValue) {
  const def = defaultValue ? ` [${defaultValue}]` : '';
  process.stdout.write(`\x1b[36m?\x1b[0m ${question}${def}: `);
  const answer = readline();
  return answer.trim() || defaultValue || '';
}

function readline() {
  const buf = Buffer.alloc(1024);
  const n = fs.readSync(0, buf, 0, buf.length);
  if (n === 0) process.exit(0);
  return buf.toString('utf-8', 0, n).trim();
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const cwd = process.cwd();
const templateRepo = 'https://github.com/nkz-os/nkz-module-template.git';

console.log('');
console.log('  \x1b[1;32m@nekazari/create-module\x1b[0m — scaffold a new Nekazari module');
console.log('');

// 1. Module identity
const moduleId = ask('Module ID (lowercase, hyphens)', 'my-module');
const displayName = ask('Display name', toDisplayName(moduleId));
const description = ask('Short description', `${displayName} module for Nekazari`);
const authorName = ask('Author name', '');
const routePath = `/${moduleId}`;

// 2. Accent color
const accentBase = ask('Accent color (hex)', '#3B82F6');

// 3. Plan
const planOptions = ['basic', 'pro', 'premium', 'enterprise'];
console.log(`  \x1b[36m?\x1b[0m Required plan: ${planOptions.map((p, i) => `${i + 1}.${p}`).join(' ')}`);
const planIdx = parseInt(ask('  Choose (1-4)', '1'), 10) - 1;
const requiredPlan = planOptions[planIdx] || 'basic';

// 4. Slots
const availableSlots = [
  'map-layer', 'context-panel', 'bottom-panel',
  'layer-toggle', 'dashboard-widget', 'entity-tree',
];
console.log(`  \x1b[36m?\x1b[0m Slots to include (space-separated numbers, e.g. "1 2"):`);
availableSlots.forEach((s, i) => console.log(`    ${i + 1}. ${s}`));
const slotChoices = ask('  Choose', '').split(/\s+/).map(Number).filter(Boolean);

const slots = {};
for (const idx of slotChoices) {
  if (idx >= 1 && idx <= availableSlots.length) {
    slots[availableSlots[idx - 1]] = [];
  }
}

// 5. Backend
const hasBackend = ask('Include backend skeleton? (y/N)', 'n').toLowerCase() === 'y';

// 6. i18n
console.log(`  \x1b[36m?\x1b[0m Languages (space-separated, e.g. "es en"):`);
console.log('    es en ca eu fr pt');
const langs = ask('  Choose', 'es en').split(/\s+/).filter(Boolean);

// ---------------------------------------------------------------------------
// Clone & scaffold
// ---------------------------------------------------------------------------

const targetDir = path.join(cwd, moduleId);
if (fs.existsSync(targetDir)) {
  console.error(`\x1b[31m✖\x1b[0m Directory '${moduleId}' already exists.`);
  process.exit(1);
}

console.log('');
console.log(`Cloning template into ${moduleId}...`);
execSync(`git clone --depth 1 ${templateRepo} "${targetDir}"`, { stdio: 'inherit' });

// Remove .git so the user can init their own
fs.rmSync(path.join(targetDir, '.git'), { recursive: true, force: true });

// ---------------------------------------------------------------------------
// Replace placeholders
// ---------------------------------------------------------------------------

const replacements = {
  MODULE_NAME: moduleId,
  MODULE_DISPLAY_NAME: displayName,
  MODULE_ROUTE: routePath,
  YOUR_ORG: 'nkz-os',
  YOUR_NAME: authorName || 'Nekazari developer',
};

function replaceInFile(filePath) {
  try {
    let content = fs.readFileSync(filePath, 'utf-8');
    let changed = false;
    for (const [from, to] of Object.entries(replacements)) {
      if (content.includes(from)) {
        content = content.replaceAll(from, to);
        changed = true;
      }
    }
    if (changed) {
      fs.writeFileSync(filePath, content);
    }
  } catch {
    // binary file or permission error — skip
  }
}

function walkAndReplace(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules') continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walkAndReplace(full);
    } else {
      replaceInFile(full);
    }
  }
}

console.log('Replacing placeholders...');
walkAndReplace(targetDir);

// Update package.json fields
const pkgPath = path.join(targetDir, 'package.json');
if (fs.existsSync(pkgPath)) {
  const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf-8'));
  pkg.name = `@nkz/${moduleId}-module`;
  pkg.nkz = pkg.nkz || {};
  pkg.nkz.moduleId = moduleId;
  pkg.description = description;
  if (authorName) pkg.author = authorName;
  fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
}

// Remove backend/ if not wanted
if (!hasBackend) {
  const backendDir = path.join(targetDir, 'backend');
  if (fs.existsSync(backendDir)) {
    fs.rmSync(backendDir, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// Install dependencies
// ---------------------------------------------------------------------------

console.log('Installing dependencies...');
execSync('pnpm install', { cwd: targetDir, stdio: 'inherit' });

// ---------------------------------------------------------------------------
// Done
// ---------------------------------------------------------------------------

console.log('');
console.log(`  \x1b[1;32m✔\x1b[0m Module '${moduleId}' created at ${targetDir}`);
console.log('');
console.log('  Next steps:');
console.log(`    cd ${moduleId}`);
console.log('    pnpm run dev          # develop with hot reload (mock platform)');
console.log('    pnpm run build:module # build MF 2.0 dist/');
console.log('');
console.log('  Deploy to local compose:');
console.log('    mc cp -r dist/ local-minio/nekazari-frontend/modules/' + moduleId + '/');
console.log('    psql ... INSERT INTO marketplace_modules ...');
console.log('    # reload http://localhost:3000');
console.log('');
console.log('  Docs: https://github.com/nkz-os/nkz/blob/main/QUICKSTART.md');

function toDisplayName(id) {
  return id
    .split('-')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}
