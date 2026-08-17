import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getSources, getSourceContent, ApiError } from "../../api/apiClient";
import type { FindingsResponse, Occurrence, SourceContent, SourceFile, SourceJump } from "../../api/types";
import { CodeViewer } from "./CodeViewer";
import { useResizableRail } from "./useResizableRail";
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

interface TreeNode { name: string; children: Map<string, TreeNode>; file: SourceFile | null; }

function segmentsOf(path: string): string[] {
  const noScheme = path.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "");
  const segs = noScheme.split("/").filter(Boolean);
  return segs.length ? segs : [path];
}

function buildTree(files: SourceFile[]): TreeNode {
  const root: TreeNode = { name: "", children: new Map(), file: null };
  for (const file of files) {
    let node = root;
    const segs = segmentsOf(file.path);
    segs.forEach((seg, i) => {
      let child = node.children.get(seg);
      if (!child) { child = { name: seg, children: new Map(), file: null }; node.children.set(seg, child); }
      if (i === segs.length - 1) child.file = file;
      node = child;
    });
  }
  return root;
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

// Total findings under a node — its own file's count plus every descendant's — so a
// directory can show that it (transitively) contains findings, even when collapsed.
function countFindingsUnder(node: TreeNode, badges: Map<string, number>): number {
  let total = node.file ? (badges.get(node.file.path) ?? 0) : 0;
  for (const child of node.children.values()) total += countFindingsUnder(child, badges);
  return total;
}

function TreeLevel({ nodes, depth, parentKey, selectedPath, onSelect, badges, collapsed, onToggle }: {
  nodes: Map<string, TreeNode>; depth: number; parentKey: string; selectedPath: string | null;
  onSelect: (path: string) => void; badges: Map<string, number>;
  collapsed: Set<string>; onToggle: (key: string) => void;
}) {
  const entries = [...nodes.values()].sort((a, b) =>
    (a.file ? 1 : 0) - (b.file ? 1 : 0) || a.name.localeCompare(b.name),
  );
  return (
    <>
      {entries.map((node) => {
        const pad = { paddingLeft: 8 + depth * 14 };
        const key = parentKey + "/" + node.name;
        const childLevel = (
          <TreeLevel nodes={node.children} depth={depth + 1} parentKey={key}
            selectedPath={selectedPath} onSelect={onSelect} badges={badges}
            collapsed={collapsed} onToggle={onToggle} />
        );
        if (node.file) {
          const file = node.file;
          const badge = badges.get(file.path);
          return (
            <div key={node.name}>
              <button type="button" style={pad}
                className={"sv-node" + (selectedPath === file.path ? " sel" : "")}
                onClick={() => onSelect(file.path)}>
                <span className="sv-caret" aria-hidden="true" />
                <span className="sv-ico"><FileIcon /></span>
                <span className="sv-node-name">{node.name}</span>
                {badge ? <span className="sv-node-badge">{badge}</span> : null}
                {file.fetch_status !== "ok" && <span className="sv-node-status">{file.fetch_status}</span>}
              </button>
              {node.children.size > 0 && childLevel}
            </div>
          );
        }
        const isCollapsed = collapsed.has(key);
        const dirFindings = countFindingsUnder(node, badges);
        return (
          <div key={node.name}>
            <button type="button" className="sv-node sv-dir" style={pad}
              aria-expanded={!isCollapsed} onClick={() => onToggle(key)}>
              <span className={"sv-caret" + (isCollapsed ? "" : " open")} aria-hidden="true">▸</span>
              <span className="sv-ico"><FolderIcon /></span>
              <span className="sv-node-name">{node.name}</span>
              {dirFindings > 0 && (
                <span className="sv-node-badge"
                  title={`${dirFindings} finding${dirFindings === 1 ? "" : "s"} in this folder`}>{dirFindings}</span>
              )}
            </button>
            {!isCollapsed && childLevel}
          </div>
        );
      })}
    </>
  );
}

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

function downloadText(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

export function SourcesPage({ data, tenantId, runId, jump }: {
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

  const badges = useMemo(() => {
    const m = new Map<string, number>();
    if (!files || !data) return m;
    for (const file of files) {
      const hashes = new Set<string>();
      for (const f of data.findings) if (f.occurrences.some((o) => matchFile(o, file))) hashes.add(f.finding_hash);
      if (hashes.size) m.set(file.path, hashes.size);
    }
    return m;
  }, [files, data]);

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

  // Memoized: the run stream now re-renders this page on every SSE event, and
  // rebuilding the whole tree each tick would jank a large file list.
  const tree = useMemo(() => buildTree(files ?? []), [files]);

  // Pretty-print: auto-on for minified content, off for already-readable source.
  // Resets per file; the beautified text is computed lazily (js-beautify) once.
  const [pretty, setPretty] = useState(false);
  const [prettyText, setPrettyText] = useState<string | null>(null);
  useEffect(() => {
    setPrettyText(null);
    setPretty(content != null && isMinified(content.content) && content.content.length <= BEAUTIFY_MAX_CHARS);
  }, [content]);
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

  const findingCount = selected ? (badges.get(selected.path) ?? 0) : 0;
  const baseName = selected ? segmentsOf(selected.path).slice(-1)[0] : "source.js";

  return (
    <div className="sv">
      <aside className={"sv-rail" + (railCollapsed ? " sv-rail-collapsed" : "")}
        style={railCollapsed ? undefined : { width: railWidth, flexBasis: railWidth }}>
        <div className="sv-rail-head">
          <div className="sv-rail-titlerow">
            <div className="sv-rail-titles">
              <h2 className="sv-rail-title">Sources</h2>
              <div className="sv-rail-sub">{files.length} file{files.length === 1 ? "" : "s"} · fetched from the target</div>
            </div>
            <button type="button" className="sv-rail-toggle" onClick={toggleRail}
              title="Collapse sources" aria-label="Collapse sources panel"><PanelIcon /></button>
          </div>
        </div>
        <div className="sv-tree">
          <TreeLevel nodes={tree.children} depth={0} parentKey="" selectedPath={selected?.path ?? null}
            onSelect={selectFile} badges={badges} collapsed={collapsed} onToggle={toggleDir} />
        </div>
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
}
