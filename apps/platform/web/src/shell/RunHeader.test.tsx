import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { RunHeader } from "./RunHeader";
import type { RunData } from "../features/progress/runData";
import type { FindingsResponse, HostsResponse, AssetsManifest, Occurrence, Finding } from "../api/types";

vi.mock("../features/progress/runData", () => ({ useRunData: vi.fn() }));
import { useRunData } from "../features/progress/runData";
const mockRunData = vi.mocked(useRunData);

const occ = (over: Partial<Occurrence> = {}): Occurrence => ({
  host: null, raw_url: null, source_path: null, line: null, col: null,
  offset_start: null, offset_end: null, evidence: null,
  engine: null, confidence: null, verified: null, asset_url: null, ...over,
});
const finding = (over: Partial<Finding> = {}): Finding => ({
  finding_hash: "h", type: "endpoint", value: "/x", path: null, severity: null,
  attributes: {}, first_stage: "analyze", revealable: false, triage: null,
  spec_status: null, occurrences: [], ...over,
});
const cov = (over: Partial<NonNullable<FindingsResponse["coverage"]>> = {}) => ({
  attributed: 10, unattributed: 0, secrets: 0, secrets_engine: null,
  sources_recovered: 0, source_map: "none", files: [], ...over,
});
const mkFindings = (over: Partial<FindingsResponse> = {}): FindingsResponse => ({
  run_id: "r", count: 0, coverage: null, spec: null, findings: [], ...over,
});
const mkAssets = (domain: string | null = "app.acme.io"): AssetsManifest => ({
  domain, status: "ok", assets: [],
});
const mkHosts = (rows: HostsResponse["hosts"] = []): HostsResponse => ({
  run_id: "r", count: rows.length, in_scope: rows.filter((h) => h.in_scope).length,
  endpoints_unattributed: 0, suspected_unattributed: 0, hosts: rows,
});
const hostRow = (host: string, in_scope: boolean) => ({
  host, in_scope, declared: false, assets: 0, endpoints: 0, suspected: 0, routes: 0, techs: 0,
});

const BASE: RunData = {
  runId: "abcdef12345678", sessionId: null,
  state: "done", stage: null, pct: null, done: 0, total: 0,
  eta: null, error: null, assets: null, findings: null, loaded: true,
  pauseRequested: false, cancelRequested: false,
  captureStatus: null, technologies: null, hosts: null, events: [],
  failureCategory: null, failureReason: null, failureHost: null,
  handleControlResult: () => {},
};

function renderHeader(partial: Partial<RunData> = {}, onOpenTuning?: () => void) {
  mockRunData.mockReturnValue({ ...BASE, ...partial });
  const router = createMemoryRouter(
    [{ path: "/runs/:id", element: <RunHeader onOpenTuning={onOpenTuning} /> }],
    { initialEntries: ["/runs/abcdef12345678"] },
  );
  render(<RouterProvider router={router} />);
}

describe("RunHeader", () => {
  beforeEach(() => { mockRunData.mockReset(); });

  it("shows the target domain from assets.domain", () => {
    renderHeader({ assets: mkAssets("app.acme.io") });
    expect(screen.getByText("app.acme.io")).toBeInTheDocument();
  });

  it("falls back to 'Current run' when domain is null", () => {
    renderHeader({ assets: mkAssets(null) });
    expect(screen.getByText("Current run")).toBeInTheDocument();
  });

  it("shows the short run id (first 8 chars)", () => {
    renderHeader();
    expect(screen.getByText("abcdef12")).toBeInTheDocument();
  });

  it("applies ok chip class for done state", () => {
    renderHeader({ state: "done" });
    expect(screen.getByText("done").className).toContain("rh-chip-ok");
  });

  it("applies bad chip class for failed state", () => {
    renderHeader({ state: "failed" });
    expect(screen.getByText("failed").className).toContain("rh-chip-bad");
  });

  it("applies warn chip class for partial state", () => {
    renderHeader({ state: "partial" });
    expect(screen.getByText("partial").className).toContain("rh-chip-warn");
  });

  it("applies muted chip class for cancelled state", () => {
    renderHeader({ state: "cancelled" });
    expect(screen.getByText("cancelled").className).toContain("rh-chip-muted");
  });

  it("applies run chip class for running state", () => {
    renderHeader({ state: "running" });
    expect(screen.getByText("running").className).toContain("rh-chip-run");
  });

  it("shows — for all pills when findings and hosts are null", () => {
    renderHeader({ findings: null, hosts: null });
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(4);
  });

  it("shows correct attribution % from coverage", () => {
    renderHeader({ findings: mkFindings({ coverage: cov({ attributed: 3, unattributed: 1 }) }) });
    expect(screen.getByText("75%")).toBeInTheDocument();
  });

  it("shows attributed/unattributed label", () => {
    renderHeader({ findings: mkFindings({ coverage: cov({ attributed: 3, unattributed: 1 }) }) });
    expect(screen.getByText("3 attributed · 1 not")).toBeInTheDocument();
  });

  it("shows endpoint count from findings", () => {
    renderHeader({
      findings: mkFindings({ findings: [finding(), finding({ finding_hash: "h2" })] }),
      hosts: mkHosts([]),
    });
    const pill = screen.getByLabelText("View endpoints");
    expect(pill.querySelector(".rh-pill-val")?.textContent).toBe("2");
  });

  it("rolls in-scope page routes into endpoint count", () => {
    renderHeader({
      findings: mkFindings({ findings: [
        finding({ type: "endpoint" }),
        finding({ finding_hash: "r1", type: "page_route", occurrences: [occ({ host: "in.io" })] }),
        finding({ finding_hash: "r2", type: "page_route", occurrences: [occ({ host: "out.io" })] }),
      ] }),
      hosts: mkHosts([hostRow("in.io", true), hostRow("out.io", false)]),
    });
    const pill = screen.getByLabelText("View endpoints");
    expect(pill.querySelector(".rh-pill-val")?.textContent).toBe("2"); // 1 endpoint + 1 in-scope route
  });

  it("shows host count", () => {
    renderHeader({ hosts: mkHosts([hostRow("a.io", true), hostRow("b.io", false)]) });
    expect(screen.getByLabelText("View hosts").querySelector(".rh-pill-val")?.textContent).toBe("2");
  });

  it("shows shadow count from spec", () => {
    renderHeader({ findings: mkFindings({
      spec: { documented: 4, shadow: 3, unresolved: 1, suffix_verify: 0, base_url_incompleteness_ratio: 0 },
    }) });
    expect(screen.getByLabelText("View shadow endpoints").querySelector(".rh-pill-val")?.textContent).toBe("3");
  });

  it("hides nudge when coverage is null", () => {
    renderHeader({ findings: mkFindings({ coverage: null }) });
    expect(screen.queryByText(/tune extraction/)).toBeNull();
  });

  it("hides nudge when fully attributed", () => {
    renderHeader({ findings: mkFindings({ coverage: cov({ attributed: 10, unattributed: 0 }) }) });
    expect(screen.queryByText(/tune extraction/)).toBeNull();
  });

  it("shows nudge with count when unattributed > 0", () => {
    renderHeader({ findings: mkFindings({ coverage: cov({ attributed: 8, unattributed: 5 }) }) });
    expect(screen.getByText(/5 calls unattributed/)).toBeInTheDocument();
    expect(screen.getByText(/tune extraction/)).toBeInTheDocument();
  });

  it("shows nudge when curtailed even if unattributed is 0", () => {
    renderHeader({ findings: mkFindings({ coverage: cov({ curtailed: true, unattributed: 0 }) }) });
    expect(screen.getByText(/tune extraction/)).toBeInTheDocument();
  });

  it("calls onOpenTuning when nudge button is clicked", () => {
    const handler = vi.fn();
    renderHeader({ findings: mkFindings({ coverage: cov({ unattributed: 3 }) }) }, handler);
    fireEvent.click(screen.getByText(/tune extraction/).closest("button")!);
    expect(handler).toHaveBeenCalledOnce();
  });
});
