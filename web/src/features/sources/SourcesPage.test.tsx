import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SourcesPage } from "./SourcesPage";
import * as api from "../../api/apiClient";
import type { FindingsResponse, Finding, Occurrence, SourceFile } from "../../api/types";

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

const UPLOAD: SourceFile = { path: "input.js", kind: "upload", fetch_status: "ok" };
const CODE = "const a = 1\nfetch('/x')\nconst b = 2\n";

function mount(sources: SourceFile[], data: FindingsResponse | null = findings) {
  vi.spyOn(api, "getSources").mockResolvedValue({ run_id: "r", count: sources.length, sources });
  vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: "input.js", content: CODE, truncated: false });
  return render(<SourcesPage data={data} tenantId="t" runId="r" />);
}

describe("SourcesPage", () => {
  it("auto-loads the first file and renders its source with a finding marker", async () => {
    mount([UPLOAD]);
    expect(await screen.findByText("fetch('/x')")).toBeInTheDocument();  // content loaded
    expect(screen.getByText("endpoint")).toBeInTheDocument();            // marker on line 2
    expect(screen.getByText(/1 finding in this file/i)).toBeInTheDocument();
  });

  it("builds a tree from asset URLs and loads a picked file's content", async () => {
    const a: SourceFile = { path: "https://acme.io/app.js", kind: "asset", fetch_status: "ok" };
    const b: SourceFile = { path: "https://acme.io/vendor.js", kind: "asset", fetch_status: "ok" };
    mount([a, b], null);
    expect(await screen.findByText("acme.io")).toBeInTheDocument();      // host dir node
    expect(screen.getByText("app.js")).toBeInTheDocument();
    await userEvent.click(screen.getByText("vendor.js").closest("button")!);
    expect(api.getSourceContent).toHaveBeenCalledWith("t", "r", "https://acme.io/vendor.js");
  });

  it("shows a not-fetched note for a pending asset and fetches no content", async () => {
    vi.spyOn(api, "getSources").mockResolvedValue({
      run_id: "r", count: 1,
      sources: [{ path: "https://acme.io/late.js", kind: "asset", fetch_status: "pending" }],
    });
    const content = vi.spyOn(api, "getSourceContent");
    render(<SourcesPage data={null} tenantId="t" runId="r" />);
    expect(await screen.findByText(/wasn't fetched/i)).toBeInTheDocument();
    expect(content).not.toHaveBeenCalled();
  });

  it("shows an empty state when the run has no source", async () => {
    mount([]);
    expect(await screen.findByText(/no source captured/i)).toBeInTheDocument();
  });

  it("flags a truncated file", async () => {
    vi.spyOn(api, "getSources").mockResolvedValue({ run_id: "r", count: 1, sources: [UPLOAD] });
    vi.spyOn(api, "getSourceContent").mockResolvedValue({ path: "input.js", content: "x", truncated: true });
    render(<SourcesPage data={null} tenantId="t" runId="r" />);
    expect(await screen.findByText(/truncated/i)).toBeInTheDocument();
  });
});
