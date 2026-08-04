// build.mjs — bundles the Preact RECON Workspace SPA (src/) into the FastAPI static
// dir so it is served at /static/workspace/. Output is committed so the API serves it
// without a build step. Mirrors the chrome-extension popup build.
import { build, context } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(root, '../api/app/static/workspace');
const watch = process.argv.includes('--watch');

/** @type {import('esbuild').BuildOptions} */
const options = {
  entryPoints: [resolve(root, 'src/main.jsx')],
  bundle: true,
  format: 'iife',
  target: ['chrome111', 'firefox110', 'safari16'],
  jsx: 'automatic',
  jsxImportSource: 'preact',
  loader: { '.js': 'jsx' },
  outfile: resolve(outDir, 'app.js'),
  minify: true,
  sourcemap: false,
  legalComments: 'none',
  logLevel: 'info'
};

if (watch) {
  const ctx = await context(options);
  await ctx.watch();
  console.log('esbuild watching web/src …');
} else {
  await build(options);
  console.log('Built api/app/static/workspace/app.js');
}
