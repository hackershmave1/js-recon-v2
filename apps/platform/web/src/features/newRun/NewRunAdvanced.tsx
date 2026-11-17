// The New Recon "Advanced" fold, closed by default. It exposes only run settings the
// API already accepts (#263): on a crawl, the per-run max file size to fetch
// (POST /runs `max_fetch_bytes`); on an upload, an optional source map sent with the
// JS file (POST /runs/upload `map`). The default and ceiling come from the server
// (GET /api/health), never hardcoded: they are env-configurable per deployment.
export const MIB = 1024 * 1024;

// Whole MiB for display: the settings are MiB multiples by default, and the input is MiB.
function toMiB(bytes: number): number {
  return Math.floor(bytes / MIB);
}

// The cap in bytes for a typed MiB value, or "invalid" when it can't be sent.
// Blank means "use the server default" (the field is omitted).
export function parseFetchCap(raw: string, ceilingBytes: number | undefined): number | null | "invalid" {
  const text = raw.trim();
  if (text === "") return null;
  const mib = Number(text);
  if (!Number.isFinite(mib) || mib <= 0) return "invalid";
  const bytes = Math.round(mib * MIB);
  if (bytes <= 0) return "invalid";
  if (ceilingBytes !== undefined && bytes > ceilingBytes) return "invalid";
  return bytes;
}

// The fetch-cap hint, naming the server's own default and ceiling when it reported them.
function capHint(defaultBytes: number | undefined, ceilingBytes: number | undefined): string {
  const limits = [
    defaultBytes !== undefined ? `${toMiB(defaultBytes)} MiB` : null,
    ceilingBytes !== undefined ? `at most ${toMiB(ceilingBytes)} MiB` : null,
  ].filter(Boolean).join("; ");
  return "Files larger than this are skipped; raise it for a very large bundle. "
    + (limits ? `Leave blank for this server's default (${limits}).` : "Leave blank for this server's default.");
}

export function NewRunAdvanced({ mode, capMiB, onCapMiB, defaultBytes, ceilingBytes, mapFile, onMapFile }: {
  mode: "crawl" | "upload";
  capMiB: string;
  onCapMiB: (value: string) => void;
  defaultBytes: number | undefined;
  ceilingBytes: number | undefined;
  mapFile: File | null;
  onMapFile: (file: File | null) => void;
}) {
  const capInvalid = parseFetchCap(capMiB, ceilingBytes) === "invalid";
  return (
    <details className="nr-advanced">
      <summary>Advanced</summary>
      <div className="nr-advanced-body">
        {mode === "crawl" ? (
          <div className="nr-field">
            <label htmlFor="max-fetch">Max file size to fetch (MiB)</label>
            <input id="max-fetch" type="number" min="1" step="1" inputMode="numeric"
              max={ceilingBytes !== undefined ? toMiB(ceilingBytes) : undefined}
              value={capMiB} onChange={(e) => onCapMiB(e.target.value)}
              placeholder={defaultBytes !== undefined ? `${toMiB(defaultBytes)} (default)` : "server default"}
              aria-invalid={capInvalid || undefined} aria-describedby="max-fetch-hint" />
            <p id="max-fetch-hint" className={capInvalid ? "sev-high nr-hint" : "muted nr-hint"}>
              {capHint(defaultBytes, ceilingBytes)}
            </p>
          </div>
        ) : (
          <div className="nr-field">
            <label htmlFor="map-file">Source map (optional)</label>
            <input id="map-file" type="file" accept=".map,.json,application/json"
              onChange={(e) => onMapFile(e.target.files?.[0] ?? null)} />
            <p className="muted nr-hint">
              {mapFile
                ? `${mapFile.name} will be sent with the JavaScript file.`
                : "The bundle's .map file, if you have it, so the original source files can be recovered."}
            </p>
          </div>
        )}
      </div>
    </details>
  );
}
