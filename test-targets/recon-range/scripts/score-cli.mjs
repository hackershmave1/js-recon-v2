import { readFile } from "node:fs/promises";
import { scoreFindings } from "./score.mjs";

const argv = process.argv.slice(2);
const args = {};
for (let i = 0; i < argv.length; i++) if (argv[i].startsWith("--")) args[argv[i].slice(2)] = argv[++i];

const base = args.base || "http://localhost:8000";
if (!args.run || !args.tenant) { console.error("usage: score-cli --run <id> --tenant <uuid> [--base http://localhost:8000]"); process.exit(2); }

const key = JSON.parse(await readFile(new URL("../answer-key.json", import.meta.url)));
const res = await fetch(`${base}/runs/${args.run}/findings`, { headers: { "X-Tenant-Id": args.tenant } });
if (!res.ok) { console.error(`findings fetch failed: ${res.status} ${res.statusText}`); process.exit(2); }
const r = scoreFindings(await res.json(), key);
console.log(JSON.stringify(r, null, 2));
console.log(r.pass ? "PASS" : "FAIL");
process.exit(r.pass ? 0 : 1);
