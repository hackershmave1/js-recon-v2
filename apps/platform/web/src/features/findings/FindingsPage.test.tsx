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
});
