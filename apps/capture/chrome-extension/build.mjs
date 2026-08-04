// build.mjs — bundles the Preact popup (src/popup) into dist/ with esbuild.
//
// Why a build step: the popup is now a Preact + JSX app. esbuild bundles it into a
// single eval-free file so it loads under MV3's default `script-src 'self'` policy.
// Output (dist/popup.js, dist/popup.css) is committed so the extension loads
// unpacked without requiring a pre-build.
import { build, context } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = dirname(fileURLToPath(import.meta.url));
const watch = process.argv.includes('--watch');

/** @type {import('esbuild').BuildOptions} */
const options = {
  entryPoints: [resolve(root, 'src/popup/main.jsx')],
  bundle: true,
  format: 'iife',
  target: ['chrome111'],
  jsx: 'automatic',
  jsxImportSource: 'preact',
  loader: { '.js': 'jsx' },
  outfile: resolve(root, 'dist/popup.js'),
  minify: true,
  sourcemap: false,
  legalComments: 'none',
  logLevel: 'info'
};

if (watch) {
  const ctx = await context(options);
  await ctx.watch();
  console.log('esbuild watching src/popup …');
} else {
  await build(options);
  console.log('Built dist/popup.js');
}
