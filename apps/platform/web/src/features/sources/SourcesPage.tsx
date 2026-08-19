import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { getSources, getSourceContent, ApiError } from "../../api/apiClient";
import type { FindingsResponse, Occurrence, SourceContent, SourceFile, SourceJump } from "../../api/types";
import { CodeViewer } from "./CodeViewer";
import { useResizableRail } from "../../shell/useResizableRail";
import { measureSync } from "../../shell/observability";
import "./sources.css";

// A finding occurrence belongs to a file when: (crawl) its asset_url equals the
// asset's URL; (source-map recovered) its source_path AND owning asset_url both
// match — a path recovered by two assets must not cross-match (design fix M4);
// (legacy) its source_path is analyze's "input.js" and it has no asset. See
// recon.probe.sources for why those are the join keys.
function matchFile(o: Occurrence, file: SourceFile): boolean {
  if (file.kind === "asset") return o.asset_url === file.path;
  if (file.kind === "source") return o.source_path === file.path && (o.asset_url ?? null) === (file.asset_url ?? null);
  return o.source_path === "input.js" && o.asset_url == null;
}

// Honest per-kind label for the Sources rail (Starbucks QA #3). The tree is a MIX:
// crawl assets (kind "asset" — only fetch_status "ok" actually fetched bytes), the
// uploaded bundle (kind "upload"), and source-map-recovered originals that carry
// findings (kind "source"). The old subheading called all of them "fetched from the
// target", which mislabeled the recovered and the non-ok assets. Every file is in
// exactly one bucket, so the parts sum to files.length. This is the BROWSABLE set —
// the Overview "Files" tile counts every analyzed file (incl. recovered originals with
// no findings, which are not enumerated here), so the two totals legitimately differ.
function sourcesSubtitle(files: SourceFile[]): string {
  const fetched = files.filter((file) => file.kind === "asset" && file.fetch_status === "ok").length;
  const notFetched = files.filter((file) => file.kind === "asset" && file.fetch_status !== "ok").length;
  const uploaded = files.filter((file) => file.kind === "upload").length;
  const recovered = files.filter((file) => file.kind === "source").length;
  const parts: string[] = [];
  if (fetched) parts.push(`${fetched} fetched`);
  if (notFetched) parts.push(`${notFetched} not fetched`);
  if (uploaded) parts.push(`${uploaded} uploaded`);
  if (recovered) parts.push(`${recovered} recovered`);
  const total = `${files.length} file${files.length === 1 ? "" : "s"}`;
  return parts.length ? `${total} · ${parts.join(" · ")}` : total;
}

// Resolve a jump (from a finding occurrence) to a stored file's `path`:
// a source-map file (matched by path + owning asset, else path only), else the
// owning asset, else the legacy "input.js" bundle.
function resolveJumpPath(j: SourceJump, files: SourceFile[]): string {
  if (j.sourcePath && j.sourcePath !== "input.js") {
    const match = files.find((f) => f.kind === "source" && f.path === j.sourcePath && (f.asset_url ?? null) === (j.assetUrl ?? null))
      ?? files.find((f) => f.kind === "source" && f.path === j.sourcePath);
    return match?.path ?? j.sourcePath;
  }
  if (j.assetUrl) {
    const asset = files.find((f) => f.path === j.assetUrl);
    if (asset) return asset.path;
  }
  return "input.js";
}

// `count` is the finding total AT and UNDER this node (a file's own count; a
// directory's aggregated descendants), precomputed once by annotateCounts so it
// isn't recursively recomputed per-directory on every render (D25).
interface TreeNode { name: string; children: Map<string, TreeNode>; file: SourceFile | null; count: number; }

function segmentsOf(path: string): string[] {
  const noScheme = path.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "");
  const segs = noScheme.split("/").filter(Boolean);
  return segs.length ? segs : [path];
}

function buildTree(files: SourceFile[]): TreeNode {
  const root: TreeNode = { name: "", children: new Map(), file: null, count: 0 };
  for (const file of files) {
    let node = root;
    const segs = segmentsOf(file.path);
    segs.forEach((seg, i) => {
      let child = node.children.get(seg);
      if (!child) { child = { name: seg, children: new Map(), file: null, count: 0 }; node.children.set(seg, child); }
      if (i === segs.length - 1) child.file = file;
      node = child;
    });
  }
  return root;
}

// One bottom-up pass: set each node's `count` = its own file's finding count plus
// every descendant's. Replaces the per-directory countFindingsUnder() that reran
// the whole subtree inside every render (D25 hot path).
function annotateCounts(node: TreeNode, fileCounts: Map<string, number>): number {
  let total = node.file ? (fileCounts.get(node.file.path) ?? 0) : 0;
  for (const child of node.children.values()) total += annotateCounts(child, fileCounts);
  node.count = total;
  return total;
}

function pushInto<K>(m: Map<K, SourceFile[]>, k: K, v: SourceFile): void {
  const a = m.get(k);
  if (a) a.push(v); else m.set(k, [v]);
}

// A source file's join key: its path AND its owning asset_url together (M4 — a path
// recovered by two assets must not cross-match). JSON.stringify gives an unambiguous,
// collision-proof composite key (proper escaping, no delimiter that could appear in a
// URL/path).
function sourceKey(path: string | null, assetUrl: string | null): string {
  return JSON.stringify([path ?? "", assetUrl ?? ""]);
}

// path -> distinct finding count, built by inverting matchFile ONCE over the
// occurrences (O(findings) instead of the old O(files x findings x occurrences)
// scan). Files are indexed by their join keys; each occurrence looks up only its
// candidate files, then matchFile re-confirms to preserve exact semantics (incl.
// the M4 same-path/different-asset rule). D25.
function fileFindingCounts(files: SourceFile[], data: FindingsResponse | null): Map<string, number> {
  const counts = new Map<string, number>();
  if (!data) return counts;
  const assetsByPath = new Map<string, SourceFile[]>();               // kind "asset", keyed by path
  const sourcesByKey = new Map<string, SourceFile[]>();               // kind "source", keyed by sourceKey()
  const uploadFiles: SourceFile[] = [];                              // legacy "input.js" bundle(s)
  for (const file of files) {
    if (file.kind === "asset") pushInto(assetsByPath, file.path, file);
    else if (file.kind === "source") pushInto(sourcesByKey, sourceKey(file.path, file.asset_url), file);
    else uploadFiles.push(file);
  }
  const hashesByPath = new Map<string, Set<string>>();
  for (const finding of data.findings) {
    for (const o of finding.occurrences) {
      const candidates: SourceFile[] = [];
      if (o.asset_url != null) { const a = assetsByPath.get(o.asset_url); if (a) candidates.push(...a); }
      const s = sourcesByKey.get(sourceKey(o.source_path, o.asset_url)); if (s) candidates.push(...s);
      if (o.source_path === "input.js" && o.asset_url == null) candidates.push(...uploadFiles);
      for (const file of candidates) {
        if (!matchFile(o, file)) continue;
        // Accumulate by tree PATH: a source path recovered from >1 asset (same path,
        // different asset_url) collapses to a single tree node, so its badge is the
        // UNION of distinct findings across those recoveries — the node's true count.
        // (The old per-file scan kept only the last such file's count, a latent
        // undercount; the Set dedups by finding_hash so a shared finding isn't
        // double-counted.)
        let set = hashesByPath.get(file.path);
        if (!set) { set = new Set(); hashesByPath.set(file.path, set); }
        set.add(finding.finding_hash);
      }
    }
  }
  for (const [path, set] of hashesByPath) counts.set(path, set.size);
  return counts;
}

// A flattened, depth-tagged row for one visible tree node. Directories carry
// `isCollapsed`; a collapsed directory's children are simply not emitted (so the
// windowed list only ever holds what's on screen). File nodes with children are
// not collapsible and always emit their descendants, matching the prior tree.
interface FlatRow { key: string; node: TreeNode; depth: number; isDir: boolean; isCollapsed: boolean; badge: number; }

function flattenTree(root: TreeNode, collapsed: Set<string>, fileCounts: Map<string, number>): FlatRow[] {
  const out: FlatRow[] = [];
  const walk = (nodes: Map<string, TreeNode>, depth: number, parentKey: string) => {
    const entries = [...nodes.values()].sort((a, b) =>
      (a.file ? 1 : 0) - (b.file ? 1 : 0) || a.name.localeCompare(b.name),
    );
    for (const node of entries) {
      const key = parentKey + "/" + node.name;
      const isDir = !node.file;
      const isCollapsed = isDir && collapsed.has(key);
      const badge = node.file ? (fileCounts.get(node.file.path) ?? 0) : node.count;
      out.push({ key, node, depth, isDir, isCollapsed, badge });
      if (node.file) { if (node.children.size > 0) walk(node.children, depth + 1, key); }
      else if (!isCollapsed) walk(node.children, depth + 1, key);
    }
  };
  walk(root.children, 0, "");
  return out;
}

const FolderIcon = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z" />
  </svg>
);
const FileIcon = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" />
  </svg>
);
// Sidebar-toggle glyph (collapse / show the file rail).
const PanelIcon = () => (
  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" />
  </svg>
);
// Jump-to-line glyph (an arrow dropping onto a baseline) for the findings jump.
const JumpIcon = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 4v10" /><path d="m7.5 11.5 4.5 4.5 4.5-4.5" /><path d="M5 20h14" />
  </svg>
);

// A source is "minified" if any line is absurdly long — a webpack/rollup bundle is
// one giant line. Such files are auto-pretty-printed; a source recovered from a
// source map is already real code and won't trip this.
function isMinified(text: string): boolean {
  return text.split("\n", 200).some((line) => line.length > 500);
}

// Lazily load the beautifier the first time a source is pretty-printed, so it never
// enters the initial bundle. Falls back to the raw code if the import/format fails.
async function formatJs(code: string): Promise<string> {
  try {
    const mod = await import("js-beautify");
    // Vite's CJS interop may put the named export directly or under `default`.
    const beautify = mod.js_beautify ?? (mod as unknown as { default?: typeof mod }).default?.js_beautify;
    return beautify ? beautify(code, { indent_size: 2, end_with_newline: false }) : code;
  } catch {
    return code;
  }
}

// Files past this size skip syntax highlighting: tokenizing multiple MiB
// synchronously would freeze the tab (design fix S2).
const HIGHLIGHT_MAX_CHARS = 200_000;

// Files past this size are NOT auto-pretty-printed and the "Pretty print" button is disabled:
// js-beautify runs SYNCHRONOUSLY on the main thread and froze the whole machine on a multi-MiB
// bundle (the server caps its own beautify at 1 MiB and serves such files raw — the client must
// not re-run that transform uncapped). Download to format offline instead.
const BEAUTIFY_MAX_CHARS = 200_000;

// Fixed tree-row height (must equal .sv-node height in sources.css) + the row count
// above which the tree is windowed. Below it the whole (small) tree renders in flow,
// which keeps the jsdom tests — and small trees — on the simple, un-windowed path.
const ROW_HEIGHT = 26;
const WINDOW_THRESHOLD = 100;

function downloadText(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

// Windowed tree body: renders only the rows intersecting the scroll viewport
// (plus a small overscan), absolutely positioned inside a full-height spacer. A
// hand-rolled fixed-height virtualizer — no dependency — since the D25 freeze was
// committing every one of hundreds-to-thousands of nodes to the DOM on each SSE
// tick. Used only above WINDOW_THRESHOLD rows.
function WindowedTree({ rows, renderRow }: { rows: FlatRow[]; renderRow: (r: FlatRow) => ReactNode }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewHeight, setViewHeight] = useState(600);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => { setScrollTop(el.scrollTop); setViewHeight(el.clientHeight || 600); };
    update();
    // ResizeObserver is absent in jsdom (and conceivably older runtimes); without it
    // the viewport falls back to the 600px default and windowing still works.
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const overscan = 12;
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - overscan);
  const end = Math.min(rows.length, Math.ceil((scrollTop + viewHeight) / ROW_HEIGHT) + overscan);
  return (
    <div className="sv-tree" ref={ref} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}>
      <div style={{ height: rows.length * ROW_HEIGHT, position: "relative" }}>
        {rows.slice(start, end).map((r, i) => (
          <div key={r.key} style={{ position: "absolute", top: (start + i) * ROW_HEIGHT, left: 0, right: 0 }}>
            {renderRow(r)}
          </div>
        ))}
      </div>
    </div>
  );
}

// The file tree. Memoized so the run's per-asset SSE ticks — which re-render the
// run layout — don't re-render (or re-flatten) the tree unless its inputs actually
// change (D25). Small trees render in flow; large trees are windowed.
const SourceTree = memo(function SourceTree({ rows, selectedPath, onSelect, onToggle }: {
  rows: FlatRow[]; selectedPath: string | null;
  onSelect: (path: string) => void; onToggle: (key: string) => void;
}) {
  const renderRow = (r: FlatRow) => {
    const pad = { paddingLeft: 8 + r.depth * 14 };
    if (!r.isDir) {
      const file = r.node.file!;
      return (
        <button type="button" key={r.key} style={pad}
          className={"sv-node" + (selectedPath === file.path ? " sel" : "")}
          onClick={() => onSelect(file.path)}>
          <span className="sv-caret" aria-hidden="true" />
          <span className="sv-ico"><FileIcon /></span>
          <span className="sv-node-name">{r.node.name}</span>
          {r.badge ? <span className="sv-node-badge">{r.badge}</span> : null}
          {file.fetch_status !== "ok" && <span className="sv-node-status">{file.fetch_status}</span>}
        </button>
      );
    }
    return (
      <button type="button" key={r.key} className="sv-node sv-dir" style={pad}
        aria-expanded={!r.isCollapsed} onClick={() => onToggle(r.key)}>
        <span className={"sv-caret" + (r.isCollapsed ? "" : " open")} aria-hidden="true">▸</span>
        <span className="sv-ico"><FolderIcon /></span>
        <span className="sv-node-name">{r.node.name}</span>
        {r.badge > 0 && (
          <span className="sv-node-badge"
            title={`${r.badge} finding${r.badge === 1 ? "" : "s"} in this folder`}>{r.badge}</span>
        )}
      </button>
    );
  };
  if (rows.length <= WINDOW_THRESHOLD) {
    return <div className="sv-tree">{rows.map(renderRow)}</div>;
  }
  return <WindowedTree rows={rows} renderRow={renderRow} />;
});

export const SourcesPage = memo(function SourcesPage({ data, tenantId, runId, jump }: {
  data: FindingsResponse | null; tenantId: string; runId: string; jump: SourceJump | null;
}) {
  const [files, setFiles] = useState<SourceFile[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selPath, setSelPath] = useState<string | null>(null);
  const [content, setContent] = useState<SourceContent | null>(null);
  const [contentState, setContentState] = useState<"idle" | "loading" | "error">("idle");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [focusLine, setFocusLine] = useState<number | null>(null);
  const toggleDir = useCallback((key: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);
  // Manually picking a file cancels a jump's line highlight.
  const selectFile = useCallback((path: string) => { setSelPath(path); setFocusLine(null); }, []);
  // Draggable + collapsible file rail (task: expand the code view).
  const { width: railWidth, collapsed: railCollapsed, toggleCollapsed: toggleRail, resizerProps } = useResizableRail();

  // A jump can arrive before `files` load, so stash the latest one and apply it
  // once files are present (the applying effect also keys on `files`).
  const pendingJump = useRef<SourceJump | null>(null);
  useEffect(() => { if (jump) pendingJump.current = jump; }, [jump]);
  useEffect(() => {
    const j = pendingJump.current;
    if (!j || !files) return;
    pendingJump.current = null;
    setSelPath(resolveJumpPath(j, files));
    setFocusLine(j.line);
  }, [jump, files]);

  useEffect(() => {
    // Clear the previous run's tree/selection so a run switch never shows stale
    // files until the refetch resolves. Also drop the old run's focus line — the
    // route has no per-run key, so a leftover focusLine would spuriously highlight
    // (and scroll to) that line number in the new run's first file. NOT the pending
    // jump ref: it is cleared when applied, and clearing it here would clobber a jump
    // passed at mount (this effect runs after the jump-stashing effect, before files load).
    setFiles(null);
    setSelPath(null);
    setListError(null);
    setCollapsed(new Set());
    setFocusLine(null);
    let live = true;
    getSources(tenantId, runId)
      .then((r) => { if (live) setFiles(r.sources); })
      .catch((e) => { if (live) setListError(e instanceof ApiError ? e.message : "Failed to load sources"); });
    return () => { live = false; };
  }, [tenantId, runId]);

  // Resolve the shown file: the clicked one, else the first viewable, else the first.
  const selected = files
    ? files.find((f) => f.path === selPath) ?? files.find((f) => f.fetch_status === "ok") ?? files[0] ?? null
    : null;

  useEffect(() => {
    setContent(null);
    if (!selected || selected.fetch_status !== "ok") { setContentState("idle"); return; }
    let live = true;
    setContentState("loading");
    // A source-map file needs its owning asset_url to disambiguate a shared path;
    // asset/upload files omit the param entirely.
    const load = selected.kind === "source"
      ? getSourceContent(tenantId, runId, selected.path, selected.asset_url)
      : getSourceContent(tenantId, runId, selected.path);
    load
      .then((c) => { if (live) { setContent(c); setContentState("idle"); } })
      .catch(() => { if (live) setContentState("error"); });
    return () => { live = false; };
  }, [tenantId, runId, selected?.path, selected?.fetch_status, selected?.kind, selected?.asset_url]);

  // path -> distinct finding count. Inverted index (D25): O(findings), not the old
  // O(files x findings) scan that ran on every large run's mount.
  const fileCounts = useMemo(() => fileFindingCounts(files ?? [], data), [files, data]);

  const marks = useMemo(() => {
    const m = new Map<number, string>();
    if (!selected || !data) return m;
    for (const f of data.findings) for (const o of f.occurrences) {
      if (matchFile(o, selected) && o.line != null && !m.has(o.line)) m.set(o.line, f.type);
    }
    return m;
  }, [selected, data]);

  // Jump-to-findings: the sorted finding lines in the current file. Clicking the
  // button advances `focusLine` to the next one (wrapping), reusing CodeViewer's
  // existing scroll-into-view + lime `.focus` highlight.
  const jumpLines = useMemo(() => [...marks.keys()].sort((a, b) => a - b), [marks]);
  const jumpToNextFinding = useCallback(() => {
    if (jumpLines.length === 0) return;
    setFocusLine((cur) => jumpLines.find((l) => l > (cur ?? -1)) ?? jumpLines[0]);
  }, [jumpLines]);

  // Rebuild + annotate the tree once per file/count change, then flatten it to a
  // windowable row list. Memoized (and the tree component is memoized) so a
  // per-asset SSE tick can't rebuild or re-render the whole tree (D25).
  const tree = useMemo(() => measureSync("sources.tree-build", 50, () => {
    const root = buildTree(files ?? []);
    annotateCounts(root, fileCounts);
    return root;
  }), [files, fileCounts]);
  const rows = useMemo(() => flattenTree(tree, collapsed, fileCounts), [tree, collapsed, fileCounts]);

  // Pretty-print: auto-on for minified content, off for already-readable source.
  // Resets per file; the beautified text is computed lazily (js-beautify) once.
  const [pretty, setPretty] = useState(false);
  const [prettyText, setPrettyText] = useState<string | null>(null);
  // A file with finding marks is server-authoritative for its lines; key the effect on
  // the boolean (not the `marks` Map identity) so it doesn't re-run when `data` gets a
  // new reference while the has-marks state is unchanged.
  const hasMarks = marks.size > 0;
  useEffect(() => {
    setPrettyText(null);
    // Don't auto-pretty-print a file that carries finding marks: the server already
    // beautified such sources before recording the finding lines, so those lines are
    // authoritative — a second client-side beautify would renumber them out from under
    // the marks (and drop them). The user can still pretty-print manually.
    setPretty(
      content != null &&
        !hasMarks &&
        isMinified(content.content) &&
        content.content.length <= BEAUTIFY_MAX_CHARS,
    );
  }, [content, hasMarks]);
  useEffect(() => {
    if (!pretty || content == null || prettyText != null || content.content.length > BEAUTIFY_MAX_CHARS) return;
    let live = true;
    void formatJs(content.content).then((out) => { if (live) setPrettyText(out); });
    return () => { live = false; };
  }, [pretty, content, prettyText]);

  if (listError) return <div className="sv-empty"><div className="sv-empty-title">Couldn't load sources</div><div>{listError}</div></div>;
  if (!files) return null;
  if (files.length === 0) {
    return <div className="sv-empty"><div className="sv-empty-title">No source captured</div><div>This run has no stored JavaScript to display yet.</div></div>;
  }

  const findingCount = selected ? (fileCounts.get(selected.path) ?? 0) : 0;
  const baseName = selected ? segmentsOf(selected.path).slice(-1)[0] : "source.js";

  return (
    <div className="sv">
      <aside className={"sv-rail" + (railCollapsed ? " sv-rail-collapsed" : "")}
        style={railCollapsed ? undefined : { width: railWidth, flexBasis: railWidth }}>
        <div className="sv-rail-head">
          <div className="sv-rail-titlerow">
            <div className="sv-rail-titles">
              <h2 className="sv-rail-title">Sources</h2>
              <div className="sv-rail-sub">{sourcesSubtitle(files)}</div>
            </div>
            <button type="button" className="sv-rail-toggle" onClick={toggleRail}
              title="Collapse sources" aria-label="Collapse sources panel"><PanelIcon /></button>
          </div>
        </div>
        <SourceTree rows={rows} selectedPath={selected?.path ?? null} onSelect={selectFile} onToggle={toggleDir} />
      </aside>

      {!railCollapsed && (
        <div className="sv-resizer" role="separator" aria-orientation="vertical"
          aria-label="Resize sources panel" title="Drag to resize" {...resizerProps} />
      )}

      <div className="sv-main">
        {selected && (
          <div className="sv-file-head">
            {railCollapsed && (
              <button type="button" className="sv-rail-toggle" onClick={toggleRail}
                title="Show sources" aria-label="Show sources panel"><PanelIcon /></button>
            )}
            <span className="sv-file-path">{selected.path}</span>
            <span className="sv-spacer" />
            {findingCount > 0 && <span className="sv-file-count">{findingCount} finding{findingCount === 1 ? "" : "s"} in this file</span>}
            {!pretty && jumpLines.length > 0 && (
              <button type="button" className="sv-jump" onClick={jumpToNextFinding}
                title="Scroll to the next finding in this file">
                <JumpIcon /> Jump to finding
              </button>
            )}
            {content && (
              <button type="button" className={"sv-pretty" + (pretty ? " on" : "")}
                aria-pressed={pretty} disabled={content.content.length > BEAUTIFY_MAX_CHARS}
                onClick={() => setPretty((p) => !p)}
                title={content.content.length > BEAUTIFY_MAX_CHARS
                  ? "Too large to format in-app — Download to format offline"
                  : pretty ? "Show the raw, unformatted source" : "Format (pretty-print) the source"}>
                <span className="sv-pretty-glyph" aria-hidden="true">{"{ }"}</span> Pretty print
              </button>
            )}
            {content && <button type="button" onClick={() => downloadText(baseName, content.content)}>{content.truncated ? "Download (partial)" : "Download"}</button>}
          </div>
        )}
        {selected && selected.fetch_status !== "ok" ? (
          <div className="sv-note">This asset wasn't fetched (status: {selected.fetch_status}) — no source to display.</div>
        ) : contentState === "loading" ? (
          <div className="sv-note">Loading source…</div>
        ) : contentState === "error" ? (
          <div className="sv-note sv-warn">Couldn't load this file's source.</div>
        ) : content ? (
          pretty && prettyText == null ? (
            <div className="sv-note">Formatting…</div>
          ) : (
            <CodeViewer
              text={pretty ? prettyText! : content.content}
              truncated={content.truncated}
              marks={pretty ? null : marks}
              focusLine={focusLine}
              // Highlight eligibility follows the RAW source size, so toggling
              // pretty-print (which only inflates whitespace) can't trip the cap.
              canHighlight={content.content.length <= HIGHLIGHT_MAX_CHARS}
            />
          )
        ) : null}
      </div>
    </div>
  );
});
