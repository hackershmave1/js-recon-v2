import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TenantProvider } from "../../tenant/TenantContext";
import { FindingsPage } from "./FindingsPage";
import type { FindingsResponse, Finding, Occurrence } from "../../api/types";

const occ = (over: Partial<Occurrence> = {}): Occurrence => ({
  host: null, raw_url: null, source_path: "app.js", line: 1, col: 1,
  offset_start: null, offset_end: null, evidence: null, engine: "vespasian",
  confidence: null, verified: null, asset_url: null, ...over,
});
const f = (over: Partial<Finding>): Finding => ({
  finding_hash: "h", type: "endpoint", value: "/x", path: null, severity: null,
  attributes: {}, first_stage: "analyze", revealable: false, triage: null,
  spec_status: null, occurrences: [occ()], ...over,
});

const data: FindingsResponse = {
  run_id: "r", count: 4,
  coverage: null,
  spec: { documented: 1, shadow: 1, unresolved: 0, suffix_verify: 0, base_url_incompleteness_ratio: 0 },
  findings: [
    f({ finding_hash: "e1", value: "GET /api/known", spec_status: { status: "documented", reason: null, matched_operation: "GET /api/known" }, occurrences: [occ({ host: "api.acme.io" })] }),
    f({ finding_hash: "e2", value: "POST /admin/wipe", spec_status: { status: "shadow", reason: null, matched_operation: null }, occurrences: [occ({ host: "api.acme.io" })] }),
    f({ finding_hash: "s1", type: "secret", value: "aws:sha256:abcd" }),
    f({ finding_hash: "p1", type: "param", value: "q" }),
  ],
};

const view = () => render(<TenantProvider><FindingsPage data={data} runId="r" onJumpToSource={() => {}} /></TenantProvider>);
const rail = () => document.querySelector(".fp-rail") as HTMLElement;

describe("FindingsPage", () => {
  it("shows the title and the shown/total count", () => {
    view();
    expect(screen.getByRole("heading", { name: "Findings" })).toBeInTheDocument();
    expect(rail()).toHaveTextContent("4 of 4 shown");
  });

  it("collapses and expands the filter rail (request #1)", async () => {
    localStorage.removeItem("recon.findingsRailCollapsed"); // start expanded regardless of prior state
    view();
    expect(rail()).not.toHaveClass("fp-rail-collapsed");
    await userEvent.click(screen.getByRole("button", { name: /collapse filters/i }));
    expect(rail()).toHaveClass("fp-rail-collapsed");
    // an expand affordance appears outside the now-hidden rail
    await userEvent.click(screen.getByRole("button", { name: /show filters/i }));
    expect(rail()).not.toHaveClass("fp-rail-collapsed");
  });

  it("labels the unconfirmed lane (endpoint_unresolved) as 'unconfirmed'", () => {
    const d: FindingsResponse = {
      run_id: "r", count: 1, coverage: null, spec: null,
      findings: [f({ finding_hash: "u1", type: "endpoint_unresolved", value: "GET /api/EXPR" })],
    };
    render(<TenantProvider><FindingsPage data={d} runId="r" onJumpToSource={() => {}} /></TenantProvider>);
    expect(screen.getByText("GET /api/EXPR")).toBeInTheDocument();
    // shown under the human label (row chip + Type facet), never the raw wire token
    expect(screen.getAllByText("unconfirmed").length).toBeGreaterThan(0);
    expect(screen.queryByText("endpoint_unresolved")).toBeNull();
  });

  it("labels the generic-call lane (endpoint_generic) as 'generic call'", () => {
    const d: FindingsResponse = {
      run_id: "r", count: 1, coverage: null, spec: null,
      findings: [f({ finding_hash: "g1", type: "endpoint_generic", value: "GET /api/generic" })],
    };
    render(<TenantProvider><FindingsPage data={d} runId="r" onJumpToSource={() => {}} /></TenantProvider>);
    expect(screen.getByText("GET /api/generic")).toBeInTheDocument();
    // shown under the human label (row chip + Type facet), never the raw wire token
    expect(screen.getAllByText("generic call").length).toBeGreaterThan(0);
    expect(screen.queryByText("endpoint_generic")).toBeNull();
  });

  it("filters by a classification facet without a re-fetch", async () => {
    view();
    // all four values present initially
    expect(screen.getByText("GET /api/known")).toBeInTheDocument();
    expect(screen.getByText("POST /admin/wipe")).toBeInTheDocument();
    // click the Classification -> shadow facet option (scoped to the rail)
    await userEvent.click(within(rail()).getByText("shadow").closest("button")!);
    expect(screen.getByText("POST /admin/wipe")).toBeInTheDocument();
    expect(screen.queryByText("GET /api/known")).toBeNull();
    expect(screen.queryByText("aws:sha256:abcd")).toBeNull();
    expect(rail()).toHaveTextContent("1 of 4 shown");
  });

  it("filters by the free-text search", async () => {
    view();
    await userEvent.type(screen.getByLabelText(/search findings/i), "admin");
    expect(screen.getByText("POST /admin/wipe")).toBeInTheDocument();
    expect(screen.queryByText("GET /api/known")).toBeNull();
  });

  it("opens a drawer with the finding's detail on row click and closes it", async () => {
    view();
    expect(screen.queryByRole("dialog")).toBeNull();
    await userEvent.click(screen.getByText("GET /api/known"));
    const drawer = screen.getByRole("dialog", { name: /finding detail/i });
    expect(within(drawer).getByText("documented")).toBeInTheDocument(); // FindingDetail rendered
    await userEvent.click(within(drawer).getByRole("button", { name: /close/i }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps the extraction-tuning knobs available (provisional home)", () => {
    view();
    expect(screen.getByText(/API spec/i)).toBeInTheDocument(); // SpecUpload mounted in the tuning block
  });

  // Slice 4: cross-run sightings badge + the ungrouped tri-state hint.
  const withFindings = (findings: Finding[]) =>
    render(
      <TenantProvider>
        <FindingsPage data={{ ...data, findings }} runId="r" onJumpToSource={() => {}} />
      </TenantProvider>,
    );

  it("renders a sightings badge with per-origin counts, dropping zero buckets", () => {
    withFindings([
      f({ finding_hash: "x", value: "GET /shared", sightings: { capture: 2, platform: 1 } }),
      f({ finding_hash: "y", value: "GET /caponly", sightings: { capture: 3, platform: 0 } }),
    ]);
    expect(screen.getByText("also seen: 2 capture · 1 platform")).toBeInTheDocument();
    expect(screen.getByText("also seen: 3 capture")).toBeInTheDocument();
  });

  it("shows no sightings badge for a finding unique to the run (all-zero)", () => {
    withFindings([f({ finding_hash: "x", value: "GET /solo", sightings: { capture: 0, platform: 0 } })]);
    expect(screen.queryByText(/also seen/)).toBeNull();
  });

  it("shows the ungrouped hint when every finding's sightings is null (no engagement)", () => {
    withFindings([
      f({ finding_hash: "x", value: "GET /a", sightings: null }),
      f({ finding_hash: "y", value: "GET /b", sightings: null }),
    ]);
    expect(screen.getByText(/group its session under an engagement/i)).toBeInTheDocument();
  });

  it("hides the ungrouped hint once the run is grouped (sightings present)", () => {
    withFindings([f({ finding_hash: "x", value: "GET /a", sightings: { capture: 0, platform: 0 } })]);
    expect(screen.queryByText(/group its session under an engagement/i)).toBeNull();
  });

  // D49: prioritization — severity pills, risk-tag chips + facet, and priority-first default sort.
  it("shows severity pills + risk-tag chips and a Risk facet", () => {
    withFindings([
      f({ finding_hash: "crit", type: "secret", value: "aws:sha256:z", severity: "critical", priority: 100 }),
      f({ finding_hash: "risk", type: "endpoint", value: "GET /api/admins", severity: "high", priority: 70, attributes: { risk_tags: ["admin"] } }),
    ]);
    expect(screen.getByText("critical")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    // "admin" renders both as a row chip and as a Risk facet option
    expect(screen.getAllByText("admin").length).toBeGreaterThan(0);
    expect(within(rail()).getByText("Risk")).toBeInTheDocument();
  });

  it("defaults to priority sort (highest first) and can switch to source order", async () => {
    withFindings([
      f({ finding_hash: "low", type: "page_route", value: "/home", severity: "low", priority: 15 }),
      f({ finding_hash: "crit", type: "secret", value: "SECRETVAL", severity: "critical", priority: 100 }),
    ]);
    const order = () => [...document.querySelectorAll(".fp-rowbtn")].map((r) => r.textContent || "");
    // default sort=priority: the critical secret precedes the low page route even though it's 2nd in the data
    let rows = order();
    expect(rows.findIndex((t) => t.includes("SECRETVAL"))).toBeLessThan(rows.findIndex((t) => t.includes("/home")));
    // switching to Default restores source order (low first)
    await userEvent.selectOptions(screen.getByLabelText(/sort findings/i), "default");
    rows = order();
    expect(rows.findIndex((t) => t.includes("/home"))).toBeLessThan(rows.findIndex((t) => t.includes("SECRETVAL")));
  });
});
