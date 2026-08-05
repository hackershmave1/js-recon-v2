import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import TerserPlugin from "minimizer-webpack-plugin";
const __dirname = dirname(fileURLToPath(import.meta.url));
export default {
  mode: "production",
  entry: resolve(__dirname, "src/main.js"),
  devtool: "source-map",
  output: {
    path: resolve(__dirname, "dist/webpack"),
    filename: "main.js",
    chunkFilename: "[name].chunk.js",
    clean: true,
  },
  // NOTE: plan's verbatim `runtimeChunk: "single"` collides with the static
  // (non-hashed) `filename: "main.js"` above — webpack tries to emit both the
  // entry chunk and the split-out runtime chunk to the literal name "main.js"
  // and aborts ("Multiple chunks emit assets to the same filename"). Splitting
  // the runtime into its own file would also break scripts/write-index.mjs's
  // single hardcoded `<script src="main.js">` tag at real-browser load time
  // (missing __webpack_require__). Keep the runtime inlined into the entry
  // chunk instead — the 3 lazy imports still emit independent chunk files via
  // chunkFilename, so the >=3-chunk invariant is unaffected.
  //
  // NOTE: webpack's built-in production minimizer (`minimizer-webpack-plugin`,
  // the current name of the package that plays the `terser-webpack-plugin`
  // role for this webpack version — same `TerserPlugin` class, already
  // resolved via webpack's own dependency tree) defaults `extractComments` to
  // true: any `/*! ... */` legal comment gets moved out to a sibling
  // `main.js.LICENSE.txt` instead of staying inline (esbuild/Vite keep such
  // comments in the same file by default, which is why the Vite build never
  // hit this). Task 3's planted secret in `src/secrets.js` relies on the
  // `/*!` comment surviving *inside* `main.js` itself (Step 14's grep only
  // reads `main*.js`), so the default extraction silently defeats that
  // acceptance gate on a fresh checkout. Explicitly disabling extraction
  // keeps the comment inline, matching Vite's behavior.
  //
  // NOTE: Terser's default `compress.reduce_vars` (on by default) inlines a
  // single-use top-level-of-scope `const` binding directly into its one call
  // site — `const KEYS = {...}; window.__reconKeys = KEYS;` becomes
  // `window.__reconKeys = {...};`. That deletes the original declaration
  // statement outright, taking any comment attached to it along for the
  // ride (the plan's `/*! ... */` legal comment trails right after `src/
  // secrets.js`'s `export const KEYS = {...};`). esbuild's minifier doesn't
  // perform this substitution, so Vite was never affected. Disabling
  // `reduce_vars` keeps the `const KEYS` statement (and its trailing
  // comment) intact, matching esbuild's behavior and letting `src/
  // secrets.js` stay a verbatim transcription of the plan.
  optimization: {
    runtimeChunk: false,
    minimizer: [new TerserPlugin({ extractComments: false, terserOptions: { compress: { reduce_vars: false } } })],
  },
};
