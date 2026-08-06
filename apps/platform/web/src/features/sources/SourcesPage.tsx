import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { getSources, getSourceContent, ApiError } from "../../api/apiClient";
import type { FindingsResponse, Occurrence, SourceContent, SourceFile, SourceJump } from "../../api/types";
import { ShellNavContext } from "../../shell/Shell";
import { highlightJsLines, type HighlightedSpan } from "./highlight";
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

// Presentational: renders `text` line-by-line. `marks` (line -> finding type) is
// null when the view is pretty-printed, since the original line numbers no longer
// map after reformatting. `focusLine` is the jumped-to line (highlighted + scrolled
// into view); it still applies to pretty-printed text even though marks don't.
function CodeViewer({ text, truncated, marks, focusLine }: {
  text: string; truncated: boolean; marks: Map<number, string> | null; focusLine?: number | null;
}) {
  const lines = useMemo(() => text.split("\n"), [text]);

  // Lazily syntax-highlight into per-line spans. Plain text until ready and on
  // failure (S3); skipped entirely for very large files (S2).
  const [highlighted, setHighlighted] = useState<HighlightedSpan[][] | null>(null);
  useEffect(() => {
    setHighlighted(null);
    if (text.length > HIGHLIGHT_MAX_CHARS) return;
    let live = true;
    void highlightJsLines(text)
      .then((out) => { if (live) setHighlighted(out); })
      .catch(() => { /* fall back to plain text */ });
    return () => { live = false; };
  }, [text]);

  // Scroll the jumped-to line into view after it renders. Re-run when highlighting
  // resolves (it reflows the line). jsdom's scrollIntoView throws, so guard it.
  const focusRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (focusLine == null) return;
    try { focusRef.current?.scrollIntoView({ block: "center" }); } catch { /* jsdom no-op */ }
  }, [focusLine, text, highlighted]);

  return (
    <div className="sv-code">
      {truncated && <div className="sv-note sv-warn">File truncated — showing a capped preview.</div>}
      {lines.map((line, i) => {
        const n = i + 1;
        const mark = marks?.get(n);
        const focused = focusLine === n;
        const spans = highlighted?.[i];
        return (
          <div key={n} ref={focused ? focusRef : undefined}
            className={"sv-line" + (mark ? " marked" : "") + (focused ? " focus" : "")}>
            <span className="sv-ln">{n}</span>
            <span className="sv-code-txt">
              {spans ? spans.map((s, j) => <span key={j} className={s.className}>{s.text}</span>) : line}
            </span>
            {mark && <span className="sv-mark">{mark}</span>}
          </div>
        );
      })}
    </div>
  );
}

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
  const shellNavigate = useContext(ShellNavContext);
  const toggleDir = useCallback((key: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);
  // Manually picking a file cancels a jump's line highlight.
  const selectFile = useCallback((path: string) => { setSelPath(path); setFocusLine(null); }, []);

  // A jump can arrive before `files` load, so stash the latest one and apply it
  // once files are present (the applying effect also keys on `files`).
  const pendingJump = useRef<SourceJump | null>(null);
  useEffect(() => { if (jump) pendingJump.current = jump; }, [jump]);
  useEffect(() => {
    const j = pendingJump.current;
    if (!j || !files) return;
    pendingJump.current = null;
    setSelPath(resolveJumpPath(j, files));
    shellNavigate("sources");
    setFocusLine(j.line);
  }, [jump, files, shellNavigate]);

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

  // Pretty-print: auto-on for minified content, off for already-readable source.
  // Resets per file; the beautified text is computed lazily (js-beautify) once.
  const [pretty, setPretty] = useState(false);
  const [prettyText, setPrettyText] = useState<string | null>(null);
  useEffect(() => {
    setPrettyText(null);
    setPretty(content != null && isMinified(content.content));
  }, [content]);
  useEffect(() => {
    if (!pretty || content == null || prettyText != null) return;
    let live = true;
    void formatJs(content.content).then((out) => { if (live) setPrettyText(out); });
    return () => { live = false; };
  }, [pretty, content, prettyText]);

  if (listError) return <div className="sv-empty"><div className="sv-empty-title">Couldn't load sources</div><div>{listError}</div></div>;
  if (!files) return null;
  if (files.length === 0) {
    return <div className="sv-empty"><div className="sv-empty-title">No source captured</div><div>This run has no stored JavaScript to display yet.</div></div>;
  }

  const tree = buildTree(files);
  const findingCount = selected ? (badges.get(selected.path) ?? 0) : 0;
  const baseName = selected ? segmentsOf(selected.path).slice(-1)[0] : "source.js";

  return (
    <div className="sv">
      <aside className="sv-rail">
        <div className="sv-rail-head">
          <h2 className="sv-rail-title">Sources</h2>
          <div className="sv-rail-sub">{files.length} file{files.length === 1 ? "" : "s"} · fetched from the target</div>
        </div>
        <div className="sv-tree">
          <TreeLevel nodes={tree.children} depth={0} parentKey="" selectedPath={selected?.path ?? null}
            onSelect={selectFile} badges={badges} collapsed={collapsed} onToggle={toggleDir} />
        </div>
      </aside>

      <div className="sv-main">
        {selected && (
          <div className="sv-file-head">
            <span className="sv-file-path">{selected.path}</span>
            <span className="sv-spacer" />
            {findingCount > 0 && <span className="sv-file-count">{findingCount} finding{findingCount === 1 ? "" : "s"} in this file</span>}
            {content && (
              <button type="button" className={"sv-pretty" + (pretty ? " on" : "")}
                aria-pressed={pretty} onClick={() => setPretty((p) => !p)}
                title={pretty ? "Show the raw, unformatted source" : "Format (pretty-print) the source"}>
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
            />
          )
        ) : null}
      </div>
    </div>
  );
}
