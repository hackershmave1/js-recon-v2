import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { OverviewPanel } from "./OverviewPanel";
import type { FindingsResponse, Finding, Occurrence, HostsResponse } from "../../api/types";

// The tiles navigate via react-router now, so the panel must render inside a router
// (with a :id param) for useNavigate/useParams to resolve.
function renderPanel(data: FindingsResponse) {
  const router = createMemoryRouter(
    [{ path: "/runs/:id", element: <OverviewPanel data={data} /> }],
    { initialEntries: ["/runs/r1"] },
  );
  render(<RouterProvider router={router} />);
}

const occ = (over: Partial<Occurrence> = {}): Occurrence => ({
  host: null, raw_url: null, source_path: null, line: null, col: null,
  offset_start: null, offset_end: null, evidence: null, engine: null,
  confidence: null, verified: null, asset_url: null, ...over,
});
const finding = (over: Partial<Finding> = {}): Finding => ({
  finding_hash: "h", type: "endpoint", value: "/x", path: null, severity: null,
  attributes: {}, first_stage: "analyze", revealable: false, triage: null,
  spec_status: null, occurrences: [], ...over,
});

// card = the <button> whose label matches; scope value assertions to it so the
// shared value "2" across cards doesn't make getByText ambiguous.
const card = (label: string) => screen.getByText(label).closest("button") as HTMLElement;

describe("OverviewPanel", () => {
  it("shows the unconfirmed lane under its label, never the raw wire token", () => {
    renderPanel({
      run_id: "r", count: 1, coverage: null, spec: null,
      findings: [finding({ finding_hash: "u1", type: "endpoint_unresolved", value: "GET /api/EXPR" })],
    });
    expect(screen.getByText("GET /api/EXPR")).toBeInTheDocument();
    expect(screen.getByText("unconfirmed")).toBeInTheDocument();
    expect(screen.queryByText("endpoint_unresolved")).toBeNull();
  });

  it("derives the four metric cards from real coverage + findings", () => {
    const data: FindingsResponse = {
      run_id: "r", count: 3,
      coverage: {
        attributed: 3, unattributed: 1, secrets: 2, secrets_engine: "ok",
        sources_recovered: 5, source_map: "capture",
        files: [{ path: "a.js", attributed: 2, unattributed: 0 }, { path: "b.js", attributed: 1, unattributed: 1 }],
      },
      spec: { documented: 1, shadow: 2, unresolved: 0, suffix_verify: 0, base_url_incompleteness_ratio: 0 },
      findings: [
        finding({ finding_hash: "e1", type: "endpoint", value: "/api/admin",
          spec_status: { status: "shadow", reason: null, matched_operation: null },
          occurrences: [occ({ source_path: "admin.js", line: 12 })] }),
        finding({ finding_hash: "s1", type: "secret", value: "AKIA..." }),
        finding({ finding_hash: "e2", type: "endpoint", value: "/api/health" }),
      ],
    };
    renderPanel(data);

    expect(within(card("Files")).getByText("2")).toBeInTheDocument();        // files.length
    expect(within(card("Endpoints")).getByText("2")).toBeInTheDocument();    // e1 + e2
    expect(within(card("Secrets")).getByText("2")).toBeInTheDocument();      // coverage.secrets
    expect(within(card("Attribution")).getByText("75%")).toBeInTheDocument(); // 3 / (3+1)
  });

  it("counts total endpoints as API + promoted (suspected) and shows the split", () => {
    renderPanel({
      run_id: "r", count: 3, coverage: null, spec: null,
      findings: [
        finding({ finding_hash: "e1", type: "endpoint", value: "GET /api/real" }),
        finding({ finding_hash: "e2", type: "endpoint", value: "GET /api/health" }),
        finding({ finding_hash: "s1", type: "endpoint_suspected", value: "GET /inbox/subjects" }),
      ],
    });
    // Headline = total (2 API + 1 promoted valid-path endpoint); the sub shows the split.
    expect(within(card("Endpoints")).getByText("3")).toBeInTheDocument();
    expect(within(card("Endpoints")).getByText("2 API · 1 endpoint")).toBeInTheDocument();
  });

  it("rolls IN-SCOPE page routes into the reachable-surface total, excluding out-of-scope links (QA #5)", () => {
    const data: FindingsResponse = {
      run_id: "r", count: 4, coverage: null, spec: null,
      findings: [
        finding({ finding_hash: "e1", type: "endpoint", value: "POST /apiproxy/v1" }),
        // relative route (no host) -> same-origin -> counts
        finding({ finding_hash: "r1", type: "page_route", value: "/account/cards", occurrences: [occ()] }),
        // absolute route on an in-scope host -> counts
        finding({ finding_hash: "r2", type: "page_route", value: "https://www.starbucks.com/menu",
          occurrences: [occ({ host: "www.starbucks.com" })] }),
        // absolute route on an OUT-of-scope sibling host -> a finding, but NOT this target's surface
        finding({ finding_hash: "r3", type: "page_route", value: "https://careers.starbucks.ca/",
          occurrences: [occ({ host: "careers.starbucks.ca" })] }),
      ],
    };
    const hostsResp: HostsResponse = {
      run_id: "r", count: 2, in_scope: 1, endpoints_unattributed: 0, suspected_unattributed: 0,
      hosts: [
        { host: "www.starbucks.com", in_scope: true, declared: false, assets: 0, endpoints: 0, suspected: 0, routes: 1, techs: 0 },
        { host: "careers.starbucks.ca", in_scope: false, declared: false, assets: 0, endpoints: 0, suspected: 0, routes: 1, techs: 0 },
      ],
    };
    const router = createMemoryRouter(
      [{ path: "/runs/:id", element: <OverviewPanel data={data} hosts={hostsResp} /> }],
      { initialEntries: ["/runs/r1"] },
    );
    render(<RouterProvider router={router} />);
    // surface = 1 API + 2 in-scope routes (relative + in-scope host); the .ca link is excluded.
    expect(within(card("Endpoints")).getByText("3")).toBeInTheDocument();
    expect(within(card("Endpoints")).getByText("1 API · 2 pages")).toBeInTheDocument();
  });

  it("counts cleartext internal-IP findings on the Internal IPs card", () => {
    // No coverage field backs this metric — the card must tally it client-side from the
    // findings list (like suspected secrets), showing the real count and no fabrication.
    const data: FindingsResponse = {
      run_id: "r", count: 3, coverage: null, spec: null,
      findings: [
        finding({ finding_hash: "ip1", type: "internal_ip", value: "10.0.0.1" }),
        finding({ finding_hash: "ip2", type: "internal_ip", value: "192.168.1.5" }),
        finding({ finding_hash: "e1", type: "endpoint", value: "/api/x" }),
      ],
    };
    renderPanel(data);
    expect(within(card("Internal IPs")).getByText("2")).toBeInTheDocument();
  });

  it("orders the shadow endpoint first and tags it", () => {
    const data: FindingsResponse = {
      run_id: "r", count: 2, coverage: null, spec: null,
      findings: [
        finding({ finding_hash: "e2", type: "endpoint", value: "/api/health" }),
        finding({ finding_hash: "e1", type: "endpoint", value: "/api/admin",
          spec_status: { status: "shadow", reason: null, matched_operation: null } }),
      ],
    };
    renderPanel(data);
    const rows = screen.getAllByRole("listitem");
    expect(within(rows[0]).getByText("/api/admin")).toBeInTheDocument();
    expect(within(rows[0]).getByText("shadow")).toBeInTheDocument();
    expect(screen.getByText("View all")).toBeInTheDocument();
  });

  it("degrades to a dash when coverage is not available yet", () => {
    const data: FindingsResponse = {
      run_id: "r", count: 1, coverage: null, spec: null,
      findings: [finding({ type: "endpoint", value: "/x" })],
    };
    renderPanel(data);
    expect(within(card("Files")).getByText("—")).toBeInTheDocument();
    expect(within(card("Attribution")).getByText("—")).toBeInTheDocument();
    expect(within(card("Endpoints")).getByText("1")).toBeInTheDocument();
    expect(within(card("Secrets")).getByText("0")).toBeInTheDocument();
    // `technologies` is absent (no prop passed) here, same as a run whose fetch
    // stage hasn't produced a fingerprint signal yet -- the card must degrade
    // to a dash rather than rendering "0" (which would misreport "scanned, found
    // none" instead of "not scanned yet").
    expect(within(card("Tech stack")).getByText("—")).toBeInTheDocument();
  });

  it("shows a dash for Tech stack when technologies is explicitly null", () => {
    const data: FindingsResponse = {
      run_id: "r", count: 1, coverage: null, spec: null,
      findings: [finding({ type: "endpoint", value: "/x" })],
    };
    const router = createMemoryRouter(
      [{ path: "/runs/:id", element: <OverviewPanel data={data} technologies={null} /> }],
      { initialEntries: ["/runs/r1"] },
    );
    render(<RouterProvider router={router} />);
    expect(within(card("Tech stack")).getByText("—")).toBeInTheDocument();
  });

  it("shows a Tech stack card counting technologies across hosts", () => {
    const data: FindingsResponse = {
      run_id: "r", count: 0, coverage: null, spec: null, findings: [],
    };
    const technologies = {
      run_id: "r", count: 3,
      hosts: { "acme.io": [
        { name: "Nginx", categories: ["Web servers"], version: "1.25.3", confidence: 100, evidence: [] },
        { name: "jQuery", categories: ["JavaScript libraries"], version: "3.5.1", confidence: 100, evidence: [] },
        { name: "React", categories: ["JavaScript frameworks"], version: null, confidence: 100, evidence: [] },
      ] },
    };
    const router = createMemoryRouter(
      [{ path: "/runs/:id", element: <OverviewPanel data={data} technologies={technologies} /> }],
      { initialEntries: ["/runs/r1"] },
    );
    render(<RouterProvider router={router} />);
    expect(within(card("Tech stack")).getByText("3")).toBeInTheDocument();
  });

  const coverage = (over: Partial<NonNullable<FindingsResponse["coverage"]>> = {}) => ({
    attributed: 3, unattributed: 1, secrets: 0, secrets_engine: "ok",
    sources_recovered: 0, source_map: "none", files: [], ...over,
  });

  it("shows a partial-extract banner when coverage is curtailed (REQ-C2 honesty)", () => {
    renderPanel({ run_id: "r", count: 0, spec: null, findings: [], coverage: coverage({ curtailed: true }) });
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.getByText(/some endpoints and hosts may be missing/i)).toBeInTheDocument();
  });

  it("hides the partial-extract banner when the run is not curtailed", () => {
    renderPanel({ run_id: "r", count: 0, spec: null, findings: [], coverage: coverage({ curtailed: false }) });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows a partial banner when a referenced source map was skipped (D32 honesty)", () => {
    renderPanel({ run_id: "r", count: 0, spec: null, findings: [], coverage: coverage({ source_map: "skipped" }) });
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.getByText(/source map couldn't be fetched/i)).toBeInTheDocument();
  });

  it("hides the source-map banner when a map was recovered or absent (not skipped)", () => {
    renderPanel({ run_id: "r", count: 0, spec: null, findings: [], coverage: coverage({ source_map: "none" }) });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("coalesces curtailed + skipped into ONE Partial banner carrying both reasons", () => {
    renderPanel({
      run_id: "r", count: 0, spec: null, findings: [],
      coverage: coverage({ curtailed: true, source_map: "skipped" }),
    });
    expect(screen.getAllByRole("status")).toHaveLength(1); // one banner, not two stacked chips
    expect(screen.getByText(/some endpoints and hosts may be missing/i)).toBeInTheDocument();
    expect(screen.getByText(/source map couldn't be fetched/i)).toBeInTheDocument();
  });
});
