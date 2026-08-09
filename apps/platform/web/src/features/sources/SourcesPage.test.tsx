import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SourcesPage } from "./SourcesPage";
import { highlightJsLines } from "./highlight";
import * as api from "../../api/apiClient";
import type { FindingsResponse, Finding, Occurrence, SourceFile, SourceJump } from "../../api/types";

// Highlighting is lazy + async and splits a line into spans; mock it out so these
// tests assert on plain text deterministically (highlight.ts has its own tests).
vi.mock("./highlight", () => ({ highlightJsLines: vi.fn(() => Promise.reject(new Error("no highlight in test"))) }));

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
  vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: "input.js", content: CODE, truncated: false });
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

  it("auto pretty-prints a minified source and toggles back to raw", async () => {
    // One giant line (the minified-bundle shape) -> auto-formatted into many lines.
    const MIN = "(function(){var a=1;function foo(){return a+2}return foo()})();" + "0;".repeat(300);
    vi.spyOn(api, "getSources").mockResolvedValue({
      run_id: "r", count: 1,
      sources: [{ path: "https://acme.io/app.js", kind: "asset", fetch_status: "ok", asset_url: null }],
    });
    vi.spyOn(api, "getSourceContent").mockResolvedValue({
      path: "https://acme.io/app.js", content: MIN, truncated: false,
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

  it("folds a directory and shows an aggregated finding badge on the folder", async () => {
    const asset: SourceFile = { path: "https://acme.io/scripts/app.js", kind: "asset", fetch_status: "ok", asset_url: null };
    const data: FindingsResponse = {
      run_id: "r", count: 1, coverage: null, spec: null,
      findings: [f({ finding_hash: "e1", occurrences: [occ({ asset_url: asset.path, line: 1 })] })],
    };
    vi.spyOn(api, "getSources").mockResolvedValue({ run_id: "r", count: 1, sources: [asset] });
    vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: asset.path, content: "x", truncated: false });
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
    vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: "input.js", content: "x", truncated: true });
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

  it("keeps highlighting eligible in pretty mode by gauging the RAW source, not the expanded text", async () => {
    // A minified one-liner UNDER the 200k highlight cap (so highlighting is
    // eligible) that beautifies to WELL OVER 200k. The bug gated on the displayed
    // (pretty-expanded) length, so it wrongly skipped highlighting in pretty mode
    // only. The guard must follow the raw source length (identical in both modes).
    const hl = vi.mocked(highlightJsLines);
    hl.mockClear();
    const raw = "function f(){" + "x=1;".repeat(23000) + "}"; // ~92k raw, one line -> minified
    vi.spyOn(api, "getSources").mockResolvedValue({
      run_id: "r", count: 1,
      sources: [{ path: "https://acme.io/app.js", kind: "asset", fetch_status: "ok", asset_url: null }],
    });
    vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: "https://acme.io/app.js", content: raw, truncated: false });
    render(<SourcesPage data={null} tenantId="t" runId="r" jump={null} />);

    const toggle = await screen.findByRole("button", { name: /pretty print/i });
    await waitFor(() => expect(toggle).toHaveAttribute("aria-pressed", "true")); // auto-on (minified)
    // Highlighting is attempted on the expanded (>200k) pretty text — proving the
    // guard measured the raw length. Pre-fix that call was skipped and never fired.
    // Generous timeout: beautifying ~90k + rendering the expanded bundle outlasts
    // waitFor's 1s default.
    await waitFor(
      () => expect(hl.mock.calls.some((c) => (c[0] as string).length > 200_000)).toBe(true),
      { timeout: 15_000 },
    );
  }, 20_000);
});
