/**
 * `nkz init <name>` — scaffold a new Nekazari module from the official template.
 *
 * What it does:
 *   1. Validates `<name>` is kebab-case.
 *   2. Clones `nkz-os/nkz-module-template` into `./<name>/` (shallow).
 *   3. Removes the `.git` directory so the new project is a fresh slate.
 *   4. Prompts for display name + route + module-kit version (with defaults).
 *   5. Find-and-replaces all placeholders across the tree:
 *        MODULE_NAME          → <name>
 *        MODULE_DISPLAY_NAME  → <Display Name>
 *        MODULE_ROUTE         → <route>
 *   6. Updates `package.json#nkz.moduleId` to <name>.
 *   7. Optionally runs `pnpm install` (skip with --skip-install).
 *   8. Prints the next steps.
 */
import { spawn } from 'node:child_process';
import { existsSync, statSync, readdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import chalk from 'chalk';
import prompts from 'prompts';

const TEMPLATE_REPO = 'https://github.com/nkz-os/nkz-module-template.git';

interface InitOptions {
  skipInstall: boolean;
  displayName?: string;
  route?: string;
}

const KEBAB_CASE = /^[a-z][a-z0-9]*(-[a-z0-9]+)*$/;
const ROUTE_PATH = /^\/[a-z0-9-/]*$/;

function fail(msg: string): never {
  console.error(chalk.red(`✖ ${msg}`));
  process.exit(1);
}

function runCommand(cmd: string, args: string[], cwd: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { cwd, stdio: 'inherit' });
    child.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} exited with code ${code}`))));
    child.on('error', reject);
  });
}

/** Recursively walk a directory yielding file paths, skipping node_modules and .git. */
function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '.git' || entry === 'dist') continue;
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      yield* walk(full);
    } else if (st.isFile()) {
      yield full;
    }
  }
}

const TEXT_EXTENSIONS = new Set([
  '.ts', '.tsx', '.js', '.jsx', '.json', '.md', '.yml', '.yaml',
  '.sql', '.sh', '.py', '.html', '.css', '.toml', '.txt', '.example',
  '.conf', '.cfg', '.ini', '.env',
]);

const TEXT_FILENAMES = new Set([
  'Dockerfile', 'Makefile', '.gitignore', '.dockerignore', '.npmrc',
  '.env.example',
]);

function isTextFile(path: string): boolean {
  const base = path.slice(path.lastIndexOf('/') + 1);
  if (TEXT_FILENAMES.has(base)) return true;
  const dot = base.lastIndexOf('.');
  if (dot === -1) return false;
  return TEXT_EXTENSIONS.has(base.slice(dot).toLowerCase());
}

function replaceInFile(
  path: string,
  replacements: Array<[RegExp, string]>,
): boolean {
  if (!isTextFile(path)) return false;
  const original = readFileSync(path, 'utf-8');
  let updated = original;
  for (const [from, to] of replacements) {
    updated = updated.replace(from, to);
  }
  if (updated !== original) {
    writeFileSync(path, updated, 'utf-8');
    return true;
  }
  return false;
}

export async function initCommand(name: string, options: InitOptions): Promise<void> {
  if (!KEBAB_CASE.test(name)) {
    fail(`module name must be kebab-case (lowercase letters, digits, hyphens), got: ${name}`);
  }

  const targetDir = resolve(process.cwd(), name);
  if (existsSync(targetDir)) {
    fail(`directory already exists: ${targetDir}`);
  }

  console.log(chalk.cyan(`nkz init — scaffolding module "${name}"`));

  // Interactive prompts for the fields we cannot infer
  let displayName = options.displayName;
  let route = options.route;
  if (!displayName || !route) {
    const answers = await prompts(
      [
        !displayName && {
          type: 'text',
          name: 'displayName',
          message: 'Display name (shown in the UI):',
          initial: name
            .split('-')
            .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
            .join(' '),
          validate: (v: string) => (v.trim().length > 0 ? true : 'display name is required'),
        },
        !route && {
          type: 'text',
          name: 'route',
          message: 'Route path (e.g. /my-module):',
          initial: `/${name}`,
          validate: (v: string) => (ROUTE_PATH.test(v) ? true : 'must start with / and be lowercase, hyphens allowed'),
        },
      ].filter(Boolean) as prompts.PromptObject[],
      { onCancel: () => process.exit(1) },
    );
    displayName = displayName ?? answers.displayName;
    route = route ?? answers.route;
  }

  // 1. Clone the template
  console.log(chalk.gray(`  → cloning ${TEMPLATE_REPO}...`));
  try {
    await runCommand('git', ['clone', '--depth', '1', TEMPLATE_REPO, targetDir], process.cwd());
  } catch (err) {
    fail(`git clone failed: ${(err as Error).message}`);
  }

  // 2. Remove .git
  rmSync(join(targetDir, '.git'), { recursive: true, force: true });

  // 3. Find-and-replace placeholders
  const replacements: Array<[RegExp, string]> = [
    [/MODULE_DISPLAY_NAME/g, displayName!],
    [/MODULE_ROUTE/g, route!.replace(/^\//, '')],
    // MODULE_NAME last because DISPLAY_NAME and ROUTE contain it as substring otherwise
    [/MODULE_NAME/g, name],
  ];

  let filesChanged = 0;
  for (const file of walk(targetDir)) {
    if (replaceInFile(file, replacements)) filesChanged++;
  }
  console.log(chalk.gray(`  → replaced placeholders in ${filesChanged} files`));

  // 4. Install
  if (!options.skipInstall) {
    console.log(chalk.gray(`  → installing dependencies (this may take a minute)...`));
    try {
      await runCommand('pnpm', ['install'], targetDir);
    } catch {
      // Fall back to npm if pnpm is unavailable
      console.log(chalk.yellow(`  ! pnpm not available, falling back to npm`));
      try {
        await runCommand('npm', ['install'], targetDir);
      } catch (err) {
        console.log(chalk.yellow(`  ! install failed, run it yourself: ${(err as Error).message}`));
      }
    }
  }

  // 5. Print next steps
  console.log();
  console.log(chalk.green(`✓ Module "${name}" created at ${targetDir}`));
  console.log();
  console.log(chalk.bold('Next steps:'));
  console.log(`  cd ${name}`);
  if (options.skipInstall) console.log(`  pnpm install`);
  console.log(`  pnpm run dev          ${chalk.gray('# Vite dev server with MockProvider')}`);
  console.log(`  pnpm run build:module ${chalk.gray('# emits dist/nkz-module.js + dist/manifest.json')}`);
  console.log();
  console.log(chalk.gray(`Edit src/Module.tsx to declare your slots, accent colour, permissions, and data dependencies.`));
}
