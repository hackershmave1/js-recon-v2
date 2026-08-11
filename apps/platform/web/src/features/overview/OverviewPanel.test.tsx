import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { OverviewPanel } from "./OverviewPanel";
import type { FindingsResponse, Finding, Occurrence } from "../../api/types";

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
  it("derives the four metric cards from real coverage + findings", () => {
    const data: FindingsResponse = {
      run_id: "r", count: 3,
      coverage: {
        attributed: 3, unattributed: 1, secrets: 2, secrets_engine: "ok",
        sources_recovered: 5, source_map: true,
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
  });
});
