import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SourcesPage } from "./SourcesPage";
import * as api from "../../api/apiClient";
import type { FindingsResponse, Finding, Occurrence, SourceFile, SourceJump } from "../../api/types";

// CodeViewer highlights the visible window via loadPrism/highlightLine; mock them out so
// these tests assert plain text deterministically (highlight.ts has its own tests).
vi.mock("./highlight", () => ({
  loadPrism: vi.fn(() => Promise.reject(new Error("no highlight in test"))),
  highlightLine: vi.fn(() => []),
  highlightJsLines: vi.fn(() => Promise.reject(new Error("no highlight in test"))),
}));
// Beautify runs in a Web Worker (jsdom has no Worker); mock the client to a deterministic
// synchronous transform that turns a minified one-liner into multiple lines.
vi.mock("./beautifyClient", () => ({
  beautify: vi.fn((code: string) => Promise.resolve(code.replace(/;/g, ";\n"))),
}));

beforeEach(() => vi.restoreAllMocks());

const occ = (o: Partial<Occurrence> = {}): Occurrence => ({
  host: null, raw_url: null, source_path: "input.js", line: 2, col: 0,
  offset_start: null, offset_end: null, evidence: null, engine: "vespasian",
  confidence: null, verified: null, asset_url: null, ...o,
});
const f = (o: Partial<Finding>): Finding => ({
  finding_hash: "h", type: "endpoint", value: "/x", path: null, severity: null,
  attributes: {}, first_stage: "analyze", revealable: false, triage: null,
  spec_status: null, occurrences: [occ()], ...o,
});
const findings: FindingsResponse = {
  run_id: "r", count: 1, coverage: null, spec: null,
  findings: [f({ finding_hash: "e1", occurrences: [occ({ line: 2 })] })],
};

const UPLOAD: SourceFile = { path: "input.js", kind: "upload", fetch_status: "ok", asset_url: null };
const CODE = "const a = 1\nfetch('/x')\nconst b = 2\n";

function mount(sources: SourceFile[], data: FindingsResponse | null = findings, jump: SourceJump | null = null) {
  vi.spyOn(api, "getSources").mockResolvedValue({ run_id: "r", count: sources.length, sources });
  vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: "input.js", content: CODE, truncated: false, formatted: true });
  return render(<SourcesPage data={data} tenantId="t" runId="r" jump={jump} />);
}

describe("SourcesPage", () => {
  it("auto-loads the first file and renders its source with a finding marker", async () => {
    mount([UPLOAD]);
    expect(await screen.findByText("fetch('/x')")).toBeInTheDocument();  // content loaded
    expect(screen.getByText("endpoint")).toBeInTheDocument();            // marker on line 2
    expect(screen.getByText(/1 finding in this file/i)).toBeInTheDocument();
  });

  it("builds a tree from asset URLs and loads a picked file's content", async () => {
    const a: SourceFile = { path: "https://acme.io/app.js", kind: "asset", fetch_status: "ok", asset_url: null };
    const b: SourceFile = { path: "https://acme.io/vendor.js", kind: "asset", fetch_status: "ok", asset_url: null };
    mount([a, b], null);
    expect(await screen.findByText("acme.io")).toBeInTheDocument();      // host dir node
    expect(screen.getByText("app.js")).toBeInTheDocument();
    await userEvent.click(screen.getByText("vendor.js").closest("button")!);
    expect(api.getSourceContent).toHaveBeenCalledWith("t", "r", "https://acme.io/vendor.js");
  });

  it("shows a not-fetched note for a pending asset and fetches no content", async () => {
    vi.spyOn(api, "getSources").mockResolvedValue({
      run_id: "r", count: 1,
      sources: [{ path: "https://acme.io/late.js", kind: "asset", fetch_status: "pending", asset_url: null }],
    });
    const content = vi.spyOn(api, "getSourceContent");
    render(<SourcesPage data={null} tenantId="t" runId="r" jump={null} />);
    expect(await screen.findByText(/wasn't fetched/i)).toBeInTheDocument();
    expect(content).not.toHaveBeenCalled();
  });

  it("shows an empty state when the run has no source", async () => {
    mount([]);
    expect(await screen.findByText(/no source captured/i)).toBeInTheDocument();
  });

  it("labels sources by kind, not all 'fetched from the target' (Starbucks QA #3)", async () => {
    const asset: SourceFile = { path: "https://acme.io/app.js", kind: "asset", fetch_status: "ok", asset_url: null };
    const pending: SourceFile = { path: "https://acme.io/late.js", kind: "asset", fetch_status: "pending", asset_url: null };
    const recovered: SourceFile = { path: "webpack:/src/api.js", kind: "source", fetch_status: "ok", asset_url: "https://acme.io/app.js" };
    mount([asset, pending, recovered], null);
    await screen.findByText("acme.io");
    // The pending asset is NOT counted as fetched, and the recovered original is not
    // called "fetched": every file falls in exactly one honest bucket.
    expect(screen.getByText("3 files · 1 fetched · 1 not fetched · 1 recovered")).toBeInTheDocument();
    expect(screen.queryByText(/fetched from the target/i)).not.toBeInTheDocument();
  });

  it("auto pretty-prints a minified source and toggles back to raw", async () => {
    // One giant line (the minified-bundle shape) -> auto-formatted into many lines.
    const MIN = "(function(){var a=1;function foo(){return a+2}return foo()})();" + "0;".repeat(300);
    vi.spyOn(api, "getSources").mockResolvedValue({
      run_id: "r", count: 1,
      sources: [{ path: "https://acme.io/app.js", kind: "asset", fetch_status: "ok", asset_url: null }],
    });
    vi.spyOn(api, "getSourceContent").mockResolvedValue({
      path: "https://acme.io/app.js", content: MIN, truncated: false, formatted: false,
    });
    render(<SourcesPage data={null} tenantId="t" runId="r" jump={null} />);

    const toggle = await screen.findByRole("button", { name: /pretty print/i });
    // aria-pressed reflects the setPretty effect, which flushes a tick after the
    // button first mounts (when content loads) — wait for it rather than racing it.
    await waitFor(() => expect(toggle).toHaveAttribute("aria-pressed", "true"));  // auto-on for minified
    await waitFor(() => expect(document.querySelectorAll(".sv-line").length).toBeGreaterThan(1));

    await userEvent.click(toggle);  // back to raw: the one-line bundle
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(document.querySelectorAll(".sv-line")).toHaveLength(1);
  });

  it("does not pretty-print already-readable source", async () => {
    mount([UPLOAD]);
    const toggle = await screen.findByRole("button", { name: /pretty print/i });
    expect(toggle).toHaveAttribute("aria-pressed", "false");  // CODE is not minified
    expect(screen.getByText("endpoint")).toBeInTheDocument();  // marks still shown
  });

  it("does not re-pretty a server-beautified marked source (marks stay aligned)", async () => {
    // A marked file is served ALREADY beautified (multi-line) by the server, so isMinified
    // is false and the client leaves it as-is — a client re-beautify would renumber the
    // marks. (A still-minified served file only ever has marks on its raw line ~1, so it
    // IS auto-formatted with those useless marks dropped — the D35 change.)
    const SERVED = "const a = fetch('/x');\n" + "const y = 2;\n".repeat(20); // multi-line, not minified
    const asset: SourceFile = { path: "https://acme.io/app.js", kind: "asset", fetch_status: "ok", asset_url: null };
    const data: FindingsResponse = {
      run_id: "r", count: 1, coverage: null, spec: null,
      findings: [f({ finding_hash: "e1", occurrences: [occ({ asset_url: asset.path, line: 1 })] })],
    };
    vi.spyOn(api, "getSources").mockResolvedValue({ run_id: "r", count: 1, sources: [asset] });
    vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: asset.path, content: SERVED, truncated: false, formatted: true });
    render(<SourcesPage data={data} tenantId="t" runId="r" jump={null} />);

    const toggle = await screen.findByRole("button", { name: /pretty print/i });
    expect(await screen.findByText("endpoint")).toBeInTheDocument();  // mark shown, not dropped
    await waitFor(() => expect(toggle).toHaveAttribute("aria-pressed", "false"));  // not re-prettied
  });

  it("folds a directory and shows an aggregated finding badge on the folder", async () => {
    const asset: SourceFile = { path: "https://acme.io/scripts/app.js", kind: "asset", fetch_status: "ok", asset_url: null };
    const data: FindingsResponse = {
      run_id: "r", count: 1, coverage: null, spec: null,
      findings: [f({ finding_hash: "e1", occurrences: [occ({ asset_url: asset.path, line: 1 })] })],
    };
    vi.spyOn(api, "getSources").mockResolvedValue({ run_id: "r", count: 1, sources: [asset] });
    vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: asset.path, content: "x", truncated: false, formatted: true });
    render(<SourcesPage data={data} tenantId="t" runId="r" jump={null} />);

    const scriptsDir = await screen.findByRole("button", { name: /scripts/i });
    expect(scriptsDir).toHaveTextContent("1");            // aggregated finding badge on the folder
    expect(screen.getByText("app.js")).toBeInTheDocument();
    await userEvent.click(scriptsDir);                     // collapse -> child hidden
    expect(screen.queryByText("app.js")).not.toBeInTheDocument();
    await userEvent.click(scriptsDir);                     // expand -> child back
    expect(screen.getByText("app.js")).toBeInTheDocument();
  });

  it("flags a truncated file", async () => {
    vi.spyOn(api, "getSources").mockResolvedValue({ run_id: "r", count: 1, sources: [UPLOAD] });
    vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: "input.js", content: "x", truncated: true, formatted: true });
    render(<SourcesPage data={null} tenantId="t" runId="r" jump={null} />);
    expect(await screen.findByText(/truncated/i)).toBeInTheDocument();
  });

  // ---- source-map (kind:"source") files, M4 asset-awareness, and the jump prop ----

  it("lists a source-map-recovered file (kind:source) in the tree", async () => {
    const src: SourceFile = {
      path: "webpack:/recon-range/src/api/social.js", kind: "source",
      fetch_status: "ok", asset_url: "https://acme.io/app.js",
    };
    mount([src], null);
    expect(await screen.findByText("social.js")).toBeInTheDocument();
  });

  it("does not mark a same-path source file whose asset_url differs (M4)", async () => {
    const src: SourceFile = {
      path: "webpack:/src/social.js", kind: "source",
      fetch_status: "ok", asset_url: "https://acme.io/app.js",
    };
    // Same source_path but recovered by a DIFFERENT asset -> must not cross-match.
    const data: FindingsResponse = {
      run_id: "r", count: 1, coverage: null, spec: null,
      findings: [f({ finding_hash: "e1", occurrences: [occ({ source_path: "webpack:/src/social.js", asset_url: "https://acme.io/other.js", line: 3 })] })],
    };
    mount([src], data);
    await screen.findByText("social.js");
    expect(screen.queryByText(/finding in this file/i)).not.toBeInTheDocument();
  });

  it("renders without crashing when a jump targets a line", async () => {
    mount([UPLOAD], findings, { sourcePath: "input.js", assetUrl: null, line: 2 });
    expect(await screen.findByText("fetch('/x')")).toBeInTheDocument();
    // focusLine applies its highlight class to the jumped-to line.
    await waitFor(() => expect(document.querySelector(".sv-line.focus")).not.toBeNull());
  });

  // ---- large-file handling (D35: a multi-MB minified bundle used to freeze the tab) ----

  it("auto-pretties a huge minified bundle off the main thread — no disabled button, no freeze", async () => {
    // The D35 fix: a multi-MB minified bundle now formats in a Web Worker and renders
    // VIRTUALIZED, instead of being stuck raw behind a disabled Pretty button.
    const HUGE = "var x=1;".repeat(200_000); // ~1.6M chars, one minified line
    vi.spyOn(api, "getSources").mockResolvedValue({
      run_id: "r", count: 1,
      sources: [{ path: "https://acme.io/big.min.js", kind: "asset", fetch_status: "ok", asset_url: null }],
    });
    vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: "https://acme.io/big.min.js", content: HUGE, truncated: false, formatted: false });
    render(<SourcesPage data={null} tenantId="t" runId="r" jump={null} />);

    const toggle = await screen.findByRole("button", { name: /pretty print/i });
    expect(toggle).not.toBeDisabled();                                            // never disabled now
    await waitFor(() => expect(toggle).toHaveAttribute("aria-pressed", "true"));  // auto-formatted
    // The beautified ~200k lines render as only a windowed handful, never all at once.
    await waitFor(() => expect(document.querySelectorAll(".sv-line").length).toBeGreaterThan(1));
    expect(document.querySelectorAll(".sv-line").length).toBeLessThan(500);
  });

  it("virtualizes a huge multi-line file (only the viewport is in the DOM)", async () => {
    const MANY = "x\n".repeat(12_000); // 12k short lines, not minified -> rendered raw, windowed
    vi.spyOn(api, "getSources").mockResolvedValue({ run_id: "r", count: 1, sources: [UPLOAD] });
    vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: "input.js", content: MANY, truncated: false, formatted: false });
    render(<SourcesPage data={null} tenantId="t" runId="r" jump={null} />);

    await waitFor(() => expect(document.querySelectorAll(".sv-line").length).toBeGreaterThan(0));
    expect(document.querySelectorAll(".sv-line").length).toBeLessThan(500);        // windowed, not ~12k
  });

  // ---- windowed file tree (D25: the freeze was committing every node to the DOM) ----

  it("windows a large file tree instead of committing every node", async () => {
    // 150 files under one host -> 151 rows, over WINDOW_THRESHOLD (100), so the tree
    // is windowed: only the rows intersecting the viewport are in the DOM, not all 150.
    const many: SourceFile[] = Array.from({ length: 150 }, (_, i) => ({
      path: `https://acme.io/chunk-${String(i).padStart(3, "0")}.js`,
      kind: "asset", fetch_status: "ok", asset_url: null,
    }));
    mount(many, null);
    await screen.findByText("acme.io");                        // tree rendered (root dir on screen)
    expect(screen.getByText(/150 files/i)).toBeInTheDocument();
    const nodes = document.querySelectorAll(".sv-node");
    expect(nodes.length).toBeGreaterThan(0);
    expect(nodes.length).toBeLessThan(151);                    // windowed, not one node per file
  });

  it("unions findings for one source path recovered from two assets (same path, different asset)", async () => {
    // The backend emits a shared source path recovered from >1 asset as distinct
    // (source_path, asset_url) files; they collapse to ONE tree node, whose badge is
    // the union of both recoveries' distinct findings (D25 by-path aggregation).
    const s1: SourceFile = { path: "webpack:/src/shared.js", kind: "source", fetch_status: "ok", asset_url: "https://acme.io/app.js" };
    const s2: SourceFile = { path: "webpack:/src/shared.js", kind: "source", fetch_status: "ok", asset_url: "https://acme.io/vendor.js" };
    const data: FindingsResponse = {
      run_id: "r", count: 2, coverage: null, spec: null,
      findings: [
        f({ finding_hash: "a1", occurrences: [occ({ source_path: s1.path, asset_url: s1.asset_url, line: 1 })] }),
        f({ finding_hash: "b2", occurrences: [occ({ source_path: s2.path, asset_url: s2.asset_url, line: 2 })] }),
      ],
    };
    mount([s1, s2], data);
    const node = await screen.findByText("shared.js");
    expect(node.closest("button")).toHaveTextContent("2"); // union of a1 + b2
  });

  it("aggregates a folder badge from an inverted index at scale", async () => {
    // Two of 120 files carry a finding; the enclosing folder badge sums them — proving
    // the O(findings) index (not the old O(files x findings) scan) still counts right.
    const many: SourceFile[] = Array.from({ length: 120 }, (_, i) => ({
      path: `https://acme.io/lib/m-${String(i).padStart(3, "0")}.js`,
      kind: "asset", fetch_status: "ok", asset_url: null,
    }));
    const data: FindingsResponse = {
      run_id: "r", count: 2, coverage: null, spec: null,
      findings: [
        f({ finding_hash: "e1", occurrences: [occ({ asset_url: many[0].path, line: 1 })] }),
        f({ finding_hash: "e2", occurrences: [occ({ asset_url: many[1].path, line: 1 })] }),
      ],
    };
    mount(many, data);
    const libDir = await screen.findByRole("button", { name: /^lib/i });
    expect(libDir).toHaveTextContent("2");                     // 2 findings aggregated under lib/
  });
});
