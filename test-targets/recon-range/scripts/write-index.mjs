import { writeFile } from "node:fs/promises";
import { join } from "node:path";
const dir = process.argv[2];
const html = `<!doctype html><html><head><meta charset="utf-8"><title>recon-range</title></head><body><main id="app"><h1>recon-range</h1><p>scroll down</p></main><script src="main.js"></script></body></html>`;
await writeFile(join(dir, "index.html"), html);
console.log(`wrote ${dir}/index.html`);
