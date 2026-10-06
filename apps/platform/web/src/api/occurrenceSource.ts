import type { Finding, Occurrence } from "./types";

// The bundle-wide placeholder the analyze stage assigns to a sighting in the raw (minified)
// bundle (backend recon.findings.analyze._SOURCE_NAME). It is NOT a real file, so it must never
// be shown as a location — the actual bundle the sighting came from is carried on `asset_url`
// (Slice Y), and a source-map-recovered original path (when present) is better still. Every
// surface that names where a finding was seen goes through this module so none of them leaks
// the placeholder (QA: Overview's "Top findings" showed `input.js:1`). Keep in sync with the
// backend constant.
export const BUNDLE_FALLBACK = "input.js";

const recoveredPath = (o: Occurrence): string | null =>
  o.source_path && o.source_path !== BUNDLE_FALLBACK ? o.source_path : null;

// The occurrence's real source for display: a source-map-recovered original path wins;
// otherwise the actual bundle URL it was sighted in; the "input.js" placeholder is only
// ever a last resort (a legacy single-blob upload that has no asset_url). `bundle` is the
// owning asset shown as a secondary tag ONLY when the primary is a distinct recovered
// path — otherwise the bundle already IS the primary and repeating it is noise.
export function occSource(o: Occurrence): { primary: string; bundle: string | null } {
  const recovered = recoveredPath(o);
  const primary = recovered ?? o.asset_url ?? o.source_path ?? o.host ?? "?";
  return { primary, bundle: recovered && o.asset_url ? o.asset_url : null };
}

// "primary[:line]" for a one-line location label, or null when the occurrence names no
// source at all (only a host) — callers then omit the label rather than invent one.
export function occLocation(o: Occurrence): string | null {
  if (!o.source_path && !o.asset_url) return null;
  return `${occSource(o).primary}${o.line != null ? `:${o.line}` : ""}`;
}

// The single occurrence a one-line summary should point at. A secret sighted in both the
// minified bundle and its recovered original is ONE finding with two occurrences; the
// recovered original names the real file, so it wins over the bundle sighting.
export function primaryOccurrence(f: Finding): Occurrence | undefined {
  return f.occurrences.find((o) => recoveredPath(o) !== null) ?? f.occurrences[0];
}
