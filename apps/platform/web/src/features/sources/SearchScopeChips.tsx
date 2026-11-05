import type { SourceFile } from "../../api/types";

export type ScopeKind = "all" | "folder" | "file";
export interface SearchScope { folder?: string; path?: string; assetUrl?: string | null }

// The tree's folder of a file (scheme stripped, empty segments dropped — the server's
// source_search.segments_of mirrors this), or null for a root-level file, which has none.
export function folderOf(path: string): string | null {
  const segs = path.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").split("/").filter(Boolean);
  return segs.length > 1 ? segs.slice(0, -1).join("/") : null;
}

// "This folder" covers its subfolders; "This file" is exact (path + the bundle it came from).
export function scopeFor(kind: ScopeKind, file: SourceFile | null): SearchScope {
  if (kind === "file" && file) return { path: file.path, assetUrl: file.asset_url };
  const folder = kind === "folder" && file ? folderOf(file.path) : null;
  return folder ? { folder } : {};
}

// Scope chips under the Sources search box. Folder/file follow the file open in the viewer.
export function SearchScopeChips({ value, file, onChange }: {
  value: ScopeKind; file: SourceFile | null; onChange: (kind: ScopeKind) => void;
}) {
  const folder = file ? folderOf(file.path) : null;
  const chips: { kind: ScopeKind; label: string; disabled: boolean; title?: string }[] = [
    { kind: "all", label: "All", disabled: false },
    { kind: "folder", label: "This folder", disabled: folder == null, title: folder ?? undefined },
    { kind: "file", label: "This file", disabled: file == null, title: file?.path },
  ];
  return (
    <div className="sv-scope" role="radiogroup" aria-label="Search scope">
      {chips.map((c) => (
        <button key={c.kind} type="button" role="radio" aria-checked={value === c.kind}
          className={"sv-scope-chip" + (value === c.kind ? " on" : "")} disabled={c.disabled}
          title={c.title} onClick={() => onChange(c.kind)}>
          {c.label}
        </button>
      ))}
    </div>
  );
}
