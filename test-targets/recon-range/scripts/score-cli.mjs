import { readFile } from "node:fs/promises";
import { scoreFindings } from "./score.mjs";

const argv = process.argv.slice(2);
const args = {};
for (let i = 0; i < argv.length; i++) if (argv[i].startsWith("--")) args[argv[i].slice(2)] = argv[++i];

const base = args.base || "http://localhost:8000";
if (!args.run) { console.error("usage: score-cli --run <id> [--username admin] [--password admin] [--tenant <uuid>] [--base http://localhost:8000]"); process.exit(2); }

// Authenticate the way the SPA + extension do: mint a session token via POST /auth/login
// and send it as `Authorization: Bearer`. The default stack ships auth ON (RECON_AUTH_SECRET
// set), where get_tenant_id derives the tenant from the token and IGNORES X-Tenant-Id — so a
// bare header 401s. The token carries the operator's tenant, which is why --tenant isn't needed
// here. Fallback: on a header-mode deployment (auth off -> login 503, or RECON_ALLOW_HEADER_TENANT=1)
// pass --tenant <uuid> to use the legacy X-Tenant-Id header instead.
const username = args.username || process.env.RECON_USERNAME || "admin";
const password = args.password || process.env.RECON_PASSWORD || "admin";
const headers = {};
const login = await fetch(`${base}/auth/login`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ username, password }),
});
if (login.ok) {
  const { token } = await login.json();
  if (!token) { console.error("login succeeded but returned no token"); process.exit(2); }
  headers.Authorization = `Bearer ${token}`;
} else if (args.tenant) {
  console.error(`login unavailable (${login.status} ${login.statusText}); falling back to X-Tenant-Id header mode`);
  headers["X-Tenant-Id"] = args.tenant;
} else {
  console.error(`login failed: ${login.status} ${login.statusText}. Pass --username/--password (default admin/admin), or --tenant <uuid> for a header-mode (auth-off) deployment.`);
  process.exit(2);
}

const key = JSON.parse(await readFile(new URL("../answer-key.json", import.meta.url)));
const res = await fetch(`${base}/runs/${args.run}/findings`, { headers });
// NOTE: set process.exitCode and fall off the end rather than process.exit() — after two
// fetches (login + findings) undici holds pooled keep-alive sockets, and a hard process.exit()
// races their teardown, crashing with a libuv assertion (wrong exit code) on Windows. Draining
// the loop exits promptly with the intended PASS/FAIL code, which is this CLI's whole contract.
if (!res.ok) {
  console.error(`findings fetch failed: ${res.status} ${res.statusText}`);
  process.exitCode = 2;
} else {
  const r = scoreFindings(await res.json(), key);
  console.log(JSON.stringify(r, null, 2));
  console.log(r.pass ? "PASS" : "FAIL");
  process.exitCode = r.pass ? 0 : 1;
}
