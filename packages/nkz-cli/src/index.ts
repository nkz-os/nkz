#!/usr/bin/env node
import { program } from 'commander';

program
  .name('nkz')
  .description('Nekazari Platform CLI — module development toolkit')
  .version('0.1.0');

program
  .command('validate')
  .description('Validate a module for correctness before deploy')
  .argument('[path]', 'Path to module directory', '.')
  .option('--strict', 'Treat warnings as errors')
  .action(async (modulePath: string, options: { strict: boolean }) => {
    const { validateCommand } = await import('./commands/validate.js');
    await validateCommand(modulePath, options);
  });

program.parse();
