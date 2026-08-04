// scopeImport.js — turn a pasted or uploaded blob of (sub)domains, in any of the formats
// a recon operator actually has on hand, into a clean, de-duped hostname list:
//   • JSON  — ["a.target.com", ...]  or  [{host|domain|hostname|name|url|subdomain: ...}]
//   • CSV   — subfinder/httpx-style rows (the domain column survives; IP/word columns drop)
//   • lines — one host per line (subfinder/amass output)
//   • commas / whitespace — "a.target.com, b.target.com"
// Full URLs are reduced to their host; ports, paths, wrapping quotes and bare IPv4 are
// dropped. Wildcards (*.target.com) are preserved since scope supports them.

function cleanDomain(raw) {
  let s = String(raw == null ? '' : raw).trim().toLowerCase();
  s = s.replace(/^["']+|["']+$/g, '');          // wrapping quotes (CSV/JSON cells)
  if (!s) return '';
  s = s.replace(/^[a-z][a-z0-9+.-]*:\/\//, '');  // strip scheme if a URL was pasted
  s = s.replace(/[/?#].*$/, '');                 // strip path / query / fragment
  s = s.replace(/:\d+$/, '');                    // strip :port
  s = s.replace(/\.+$/, '');                     // trailing dot(s)
  if (!s || !s.includes('.')) return '';
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(s)) return '';  // reject bare IPv4
  if (!/^[a-z0-9.*_-]+$/.test(s)) return '';         // domain chars (+ * wildcard)
  return s;
}

export function parseDomainList(input) {
  const text = String(input == null ? '' : input).trim();
  if (!text) return [];

  let tokens = [];

  // JSON first (array of strings, array of objects, or an object of values).
  if (/^[[{]/.test(text)) {
    try {
      const parsed = JSON.parse(text);
      const values = Array.isArray(parsed) ? parsed : Object.values(parsed);
      for (const v of values) {
        if (typeof v === 'string') tokens.push(v);
        else if (v && typeof v === 'object') {
          const cand = v.host || v.domain || v.hostname || v.name || v.url || v.subdomain;
          if (typeof cand === 'string') tokens.push(cand);
        }
      }
    } catch (e) {
      tokens = [];   // not valid JSON — fall through to delimiter splitting
    }
  }

  // Delimiter split covers CSV, one-per-line, comma- and whitespace-separated in one pass.
  // A CSV's extra columns (IPs, source names) are filtered out by cleanDomain.
  if (tokens.length === 0) tokens = text.split(/[\s,]+/);

  const out = [];
  const seen = new Set();
  for (const t of tokens) {
    const d = cleanDomain(t);
    if (d && !seen.has(d)) { seen.add(d); out.push(d); }
  }
  return out;
}

// Read an uploaded File to text (Promise). Rejects on read error.
export function readFileText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error || new Error('Could not read file'));
    reader.readAsText(file);
  });
}
