import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { join, extname, normalize } from "node:path";
const dir = process.argv[2];
const port = Number(process.argv[3] || 8080);
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".map": "application/json", ".json": "application/json", ".css": "text/css" };
createServer(async (req, res) => {
  try {
    let p = decodeURIComponent(req.url.split("?")[0]);
    if (p.endsWith("/")) p += "index.html";
    const file = join(dir, normalize(p).replace(/^(\.\.[/\\])+/, ""));
    const body = await readFile(file);
    res.writeHead(200, { "content-type": TYPES[extname(file)] || "application/octet-stream" });
    res.end(body);
  } catch { res.writeHead(404); res.end("not found"); }
}).listen(port, () => console.log(`serving ${dir} on http://localhost:${port}`));
