#!/usr/bin/env node
import { program } from 'commander';

program
  .name('nkz')
  .description('Nekazari Platform CLI — module development toolkit')
  .version('0.2.0');

program
  .command('init')
  .description('Scaffold a new Nekazari module from the official template')
  .argument('<name>', 'Module name (kebab-case)')
  .option('--display-name <name>', 'Display name shown in the UI')
  .option('--route <path>', 'Route path mounted in the host, e.g. /my-module')
  .option('--skip-install', 'Skip running pnpm/npm install', false)
  .action(async (name: string, options: { displayName?: string; route?: string; skipInstall: boolean }) => {
    const { initCommand } = await import('./commands/init.js');
    await initCommand(name, options);
  });

program
  .command('validate')
  .description('Validate a module for correctness before deploy')
  .argument('[path]', 'Path to module directory', '.')
  .option('--strict', 'Treat warnings as errors')
  .action(async (modulePath: string, options: { strict: boolean }) => {
    const { validateCommand } = await import('./commands/validate.js');
    await validateCommand(modulePath, options);
  });

program
  .command('dev')
  .description('Start development server with host shell + HMR')
  .argument('[path]', 'Path to module directory', '.')
  .action(async (modulePath: string) => {
    const { devCommand } = await import('./commands/dev.js');
    await devCommand(modulePath);
  });

program.parse();
