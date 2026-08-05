import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
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
  optimization: { runtimeChunk: false },
};
