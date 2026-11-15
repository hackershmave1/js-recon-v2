// Every JSX component a popup file renders must be imported or defined in that file. esbuild bundles
// an undefined identifier without complaint, so the popup only fails when the screen renders (a
// blank Settings screen shipped once because a cleanup removed an icon import another section used).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const dir = new URL('../src/popup/', import.meta.url);
const files = [];
const walk = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) {
  const p = path.join(d, e.name);
  if (e.isDirectory()) walk(p); else if (p.endsWith('.jsx')) files.push(p);
} };
walk(new URL('.', dir).pathname.replace(/^\/([A-Za-z]:)/, '$1'));

for (const file of files) {
  const src = fs.readFileSync(file, 'utf8');
  const used = new Set([...src.matchAll(/<([A-Z]\w*)[\s/>]/g)].map((m) => m[1]));
  const declared = new Set();
  for (const m of src.matchAll(/import\s+([^;]*?)\s+from\s+['"]/g)) {
    for (const part of m[1].replace(/[{}]/g, ',').split(',')) {
      const id = part.trim().split(/\s+as\s+/).pop();
      if (id) declared.add(id);
    }
  }
  for (const m of src.matchAll(/(?:function|const)\s+([A-Z]\w*)/g)) declared.add(m[1]);
  for (const name of used) {
    assert.ok(declared.has(name), `${path.basename(file)} renders <${name}> but never imports or defines it`);
  }
}
console.log(`ok - popup JSX identifiers resolve (${files.length} files)`);
