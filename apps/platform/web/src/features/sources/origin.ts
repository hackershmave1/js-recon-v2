// Third-party code vs app code (GLOSSARY), by package path — the client mirror of the server's
// recon.findings.origin (shared test vectors keep the two in step). The Sources tree groups
// third-party files under one collapsed folder; the server hides their findings and leaves them
// out of search.

export const THIRD_PARTY_ROOT = "Third-party packages";

const SEGMENT = /(?:^|\/)(node_modules|\.yarn|bower_components)\//;
// A lookahead (not a consumed "/"): a package nested inside another is found, the innermost wins.
const PACKAGE = /(?:^|\/)(?:node_modules|bower_components)\/((?:@[^/]+\/)?[^/.][^/]*)(?=\/)/g;
const PNPM = /(?:^|\/)\.pnpm\/((?:@[^/@]+\+)?[^/@]+)@([^/_]+)/;

export function packageOf(path: string): { name: string; version: string | null } | null {
  if (!path || path.includes("://") || !SEGMENT.test(path)) return null;
  const names = [...path.matchAll(PACKAGE)].map((m) => m[1]);
  if (names.length === 0) return null;
  const name = names[names.length - 1];
  const pnpm = PNPM.exec(path);
  const version = pnpm && pnpm[1].replace("+", "/") === name ? pnpm[2] : null;
  return { name, version };
}

function matches(name: string, pattern: string): boolean {
  const p = pattern.trim();
  return p.endsWith("/*") ? name.startsWith(p.slice(0, -1)) : name === p;
}

export function isThirdParty(path: string, appPackages: readonly string[] = []): boolean {
  const pkg = packageOf(path);
  return pkg != null && !appPackages.some((p) => matches(pkg.name, p));
}

// Where a file sits in the tree: a third-party file goes under
// "Third-party packages / <name version> / <path inside the package>"; everything else keeps
// `segments` (the tree's own folder rule).
export function treeSegments(path: string, segments: string[], appPackages: readonly string[]): {
  segs: string[]; packageName: string | null;
} {
  if (!isThirdParty(path, appPackages)) return { segs: segments, packageName: null };
  const pkg = packageOf(path)!;
  // Everything after the innermost package directory (not after the last occurrence of its NAME:
  // `node_modules/foo/lib/foo/index.js` must stay `lib/foo/index.js`).
  const last = [...path.matchAll(PACKAGE)].pop()!;
  const inside = path.slice(last.index! + last[0].length).split("/").filter(Boolean);
  const label = pkg.version ? `${pkg.name} ${pkg.version}` : pkg.name;
  return { segs: [THIRD_PARTY_ROOT, label, ...inside], packageName: pkg.name };
}
