import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import chalk from 'chalk';

export async function devCommand(modulePath: string): Promise<void> {
  const absPath = resolve(modulePath);
  const hostShellPath = resolve(__dirname, '..', 'dev-server', 'host-shell.html');

  console.log(chalk.blue(`Starting dev server for module at ${absPath}\n`));

  // Start Vite dev server for the module
  const vite = spawn('npx', ['vite', '--port', '5173', '--strictPort'], {
    cwd: absPath,
    stdio: 'pipe',
    shell: true,
  });

  vite.stderr?.on('data', (data: Buffer) => {
    const msg = data.toString();
    if (!msg.includes('DeprecationWarning')) {
      process.stderr.write(msg);
    }
  });

  // Start lightweight host shell HTTP server
  const hostShell = readFileSync(hostShellPath, 'utf-8');
  const hostPort = 5174;

  const server = createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(
      hostShell.replace(
        'MODULE_BUNDLE_URL',
        'http://localhost:5173/src/moduleEntry.ts',
      ),
    );
  });

  server.listen(hostPort, () => {
    console.log(chalk.green(`Dev host shell: http://localhost:${hostPort}`));
    console.log(chalk.dim('Module loads as IIFE inside lightweight host shell.'));
    console.log(chalk.dim('HMR is active — save a file to reload.\n'));
  });

  const cleanup = () => {
    vite.kill();
    server.close();
    process.exit(0);
  };
  process.on('SIGINT', cleanup);
  process.on('SIGTERM', cleanup);
}
