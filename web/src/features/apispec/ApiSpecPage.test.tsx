import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiSpecPage } from "./ApiSpecPage";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type {
  FindingsResponse, Finding, Occurrence, ReconstructedRequest, RequestsResponse,
} from "../../api/types";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

const occ = (over: Partial<Occurrence> = {}): Occurrence => ({
  host: "api.acme.io", raw_url: null, source_path: "app.js", line: 42, col: 1,
  offset_start: null, offset_end: null, evidence: null, engine: "vespasian",
  confidence: null, verified: null, asset_url: null, ...over,
});
const finding = (over: Partial<Finding>): Finding => ({
  finding_hash: "h", type: "endpoint", value: "GET /api/users", path: null, severity: null,
  attributes: {}, first_stage: "analyze", revealable: false, triage: null,
  spec_status: null, occurrences: [occ()], ...over,
});
const req = (over: Partial<ReconstructedRequest>): ReconstructedRequest => ({
  operation: "GET /api/users", method: "GET", path: "/api/users", hosts: ["api.acme.io"],
  query_params: [{ name: "page", example: "1" }], body_params: [], content_type: null,
  example_url: null, probeable: true, endpoint_hashes: ["h1"], artifacts: null, ...over,
});

const LOGIN = req({
  operation: "POST /auth/login", method: "POST", path: "/auth/login",
  query_params: [], endpoint_hashes: ["h2"],
});

const findings: FindingsResponse = {
  run_id: "r", count: 2, coverage: null, spec: null,
  findings: [
    finding({ finding_hash: "h1", value: "GET /api/users",
      spec_status: { status: "shadow", reason: null, matched_operation: null },
      occurrences: [occ({ source_path: "assets/app.js", line: 128 })] }),
    finding({ finding_hash: "h2", value: "POST /auth/login" }),
  ],
};

function mount(reqs: ReconstructedRequest[], data: FindingsResponse | null = findings) {
  vi.spyOn(api, "getRequests").mockResolvedValue(
    { run_id: "r", count: reqs.length, requests: reqs } as RequestsResponse,
  );
  return render(<TenantProvider><ApiSpecPage data={data} runId="r" /></TenantProvider>);
}

describe("ApiSpecPage", () => {
  it("groups operations by path tag and shows the operation count", async () => {
    mount([req({}), LOGIN]);
    expect(await screen.findByText("/api")).toBeInTheDocument();   // group header
    expect(screen.getByText("/auth")).toBeInTheDocument();
    expect(screen.getByText(/2 operations · reconstructed/i)).toBeInTheDocument();
  });

  it("auto-selects the first operation and shows its params + trace-to-source", async () => {
    mount([req({})]);
    expect(await screen.findByText("page")).toBeInTheDocument();          // query param name
    expect(screen.getByText("query")).toBeInTheDocument();                // location chip
    expect(screen.getByText("assets/app.js:128")).toBeInTheDocument();    // from the linked finding
    expect(screen.getByText("shadow")).toBeInTheDocument();               // spec_status chip
  });

  it("opens the linked finding in a drawer", async () => {
    mount([req({})]);
    await screen.findByText("page");
    await userEvent.click(screen.getByRole("button", { name: /linked finding/i }));
    expect(screen.getByRole("dialog", { name: /finding detail/i })).toBeInTheDocument();
  });

  it("switches the detail when another operation is picked", async () => {
    mount([req({}), LOGIN]);
    await screen.findByText("/auth");
    // click the /auth/login row (its accessible name has no guaranteed space
    // between the method + path spans, so target the row via its path text)
    await userEvent.click(screen.getByText("/auth/login").closest("button")!);
    expect(await screen.findByText(/no parameters observed/i)).toBeInTheDocument();
  });

  it("filters the operation list by the free-text query", async () => {
    mount([req({}), LOGIN]);
    await screen.findByText("/api");
    await userEvent.type(screen.getByLabelText(/filter operations/i), "auth");
    expect(screen.getByText("/auth")).toBeInTheDocument();
    expect(screen.queryByText("/api")).toBeNull();
  });

  it("shows an empty state when nothing was reconstructed", async () => {
    mount([]);
    expect(await screen.findByText(/nothing reconstructed/i)).toBeInTheDocument();
  });

  it("keeps the Export spec action in the header", async () => {
    mount([req({})]);
    expect(await screen.findByRole("button", { name: /export spec/i })).toBeInTheDocument();
  });
});
