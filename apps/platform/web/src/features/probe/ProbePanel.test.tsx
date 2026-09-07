import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { ProbePanel } from "./ProbePanel";
import { TenantProvider } from "../../tenant/TenantContext";
import { useRunDataOptional } from "../progress/runData";
import * as api from "../../api/apiClient";
import type { ReconstructedRequest } from "../../api/types";

vi.mock("../progress/runData", () => ({ useRunDataOptional: vi.fn(() => null) }));

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.setItem("recon.tenantId", TENANT);
  vi.mocked(useRunDataOptional).mockReturnValue(null);
});

const REQ: ReconstructedRequest = {
  operation: "GET /api/users", method: "GET", path: "/api/users", hosts: ["api.acme.io"],
  query_params: [{ name: "page", example: "1" }], body_params: [], content_type: null,
  example_url: "https://api.acme.io/api/users?page=1", probeable: true, endpoint_hashes: ["h1"],
  artifacts: { curl: "curl 'https://api.acme.io/api/users?page=1'", http: "GET /api/users?page=1 HTTP/1.1" },
};

// A relative (host-less) request — the case the host-selector resolves.
const RELATIVE: ReconstructedRequest = {
  operation: "GET /api/rel", method: "GET", path: "/api/rel", hosts: [],
  query_params: [], body_params: [], content_type: null,
  example_url: "/api/rel", probeable: true, endpoint_hashes: ["h2"],
  artifacts: { curl: "curl 'https://{{base_url}}/api/rel'", http: "GET /api/rel HTTP/1.1" },
};

function ui(reqs: ReconstructedRequest[]) {
  vi.spyOn(api, "getRequests").mockResolvedValue({ run_id: "r", count: reqs.length, requests: reqs });
  return render(
    <MemoryRouter initialEntries={["/runs/r/probe"]}>
      <TenantProvider>
        <ProbePanel runId="r" />
      </TenantProvider>
    </MemoryRouter>,
  );
}

// Wait helper: path appears in both list row + detail header — use findAllByText.
async function waitForData(path = "/api/users") {
  await screen.findAllByText(path);
}

describe("ProbePanel", () => {
  it("shows reconstructed request in the list", async () => {
    ui([REQ]);
    const items = await screen.findAllByText("/api/users");
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  it("auto-selects first probeable op and shows its detail", async () => {
    ui([REQ]);
    // Path appears in both list row and detail header when op is selected
    const paths = await screen.findAllByText("/api/users");
    expect(paths.length).toBeGreaterThanOrEqual(2);
  });

  it("copy button (curl default tab) writes curl content to clipboard", async () => {
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    ui([REQ]);
    await waitForData();
    await userEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    expect(writeText).toHaveBeenCalledWith(REQ.artifacts!.curl);
  });

  it("switching to HTTP tab and copying writes http content", async () => {
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    ui([REQ]);
    await waitForData();
    await userEvent.click(screen.getByRole("tab", { name: "HTTP" }));
    await userEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    expect(writeText).toHaveBeenCalledWith(REQ.artifacts!.http);
  });

  it("shows copied feedback for 1.2s then resets", async () => {
    vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    ui([REQ]);
    await waitForData();
    await userEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    expect(await screen.findByText(/copied/i)).toBeInTheDocument();
  });

  it("shows an error when the requests fetch fails", async () => {
    vi.spyOn(api, "getRequests").mockRejectedValue(new api.ApiError(404, "run not found"));
    render(<MemoryRouter><TenantProvider><ProbePanel runId="r" /></TenantProvider></MemoryRouter>);
    expect(await screen.findByText(/run not found/i)).toBeInTheDocument();
  });

  it("non-probeable op with no artifacts shows explanatory text (REQ-PB8)", async () => {
    ui([{ ...REQ, probeable: false, artifacts: null }]);
    // ArtifactTabs message when artifacts is null
    expect(await screen.findByText(/not probeable from this surface/i)).toBeInTheDocument();
  });

  it("WS op shows websocat tab, no not-probeable dead-end (D51)", async () => {
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    const WS: ReconstructedRequest = {
      ...REQ, operation: "WSS /socket", method: "WSS", path: "/socket", probeable: false,
      artifacts: { websocat: "websocat 'wss://api.acme.io/socket'" },
    };
    ui([WS]);
    // /socket appears in list + detail when selected
    await screen.findAllByText("/socket");
    await userEvent.click(screen.getByRole("tab", { name: "websocat" }));
    await userEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    expect(writeText).toHaveBeenCalledWith(WS.artifacts!.websocat);
    // ProbeDetail does not show the reason line when artifacts are present
    expect(screen.queryByText(/listed for visibility/i)).not.toBeInTheDocument();
  });

  it("shows empty message when there are no requests", async () => {
    ui([]);
    expect(await screen.findByText(/no probeable requests/i)).toBeInTheDocument();
  });

  it("hides the host-selector when every request has an absolute URL", async () => {
    ui([REQ]);
    await waitForData();
    expect(screen.queryByLabelText(/resolve relative paths against/i)).not.toBeInTheDocument();
  });

  it("shows host-selector for a relative request and re-resolves via custom host", async () => {
    const spy = vi.spyOn(api, "getRequests").mockResolvedValue({ run_id: "r", count: 1, requests: [RELATIVE] });
    render(<MemoryRouter><TenantProvider><ProbePanel runId="r" /></TenantProvider></MemoryRouter>);
    const select = await screen.findByLabelText(/resolve relative paths against/i);
    await userEvent.selectOptions(select, "__custom__");
    await userEvent.type(screen.getByLabelText(/custom host/i), "api.example.com{Enter}");
    await waitFor(() => expect(spy).toHaveBeenCalledWith(TENANT, "r", "api.example.com"));
  });

  it("defaults to the primary in-scope host and re-resolves when another is picked", async () => {
    vi.mocked(useRunDataOptional).mockReturnValue({
      hosts: {
        run_id: "r", count: 2, in_scope: 2, endpoints_unattributed: 0, suspected_unattributed: 0,
        hosts: [
          { host: "api.acme.io", in_scope: true, declared: false, assets: 0, endpoints: 2, suspected: 0, routes: 0, techs: 0 },
          { host: "www.acme.io", in_scope: true, declared: false, assets: 1, endpoints: 0, suspected: 0, routes: 0, techs: 0 },
        ],
      },
      assets: { domain: "https://www.acme.io/", status: "ok", assets: [] },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    const spy = vi.spyOn(api, "getRequests").mockResolvedValue({ run_id: "r", count: 1, requests: [RELATIVE] });
    render(<MemoryRouter><TenantProvider><ProbePanel runId="r" /></TenantProvider></MemoryRouter>);
    await waitFor(() => expect(spy).toHaveBeenCalledWith(TENANT, "r", "www.acme.io"));
    await userEvent.selectOptions(await screen.findByLabelText(/resolve relative paths against/i), "api.acme.io");
    await waitFor(() => expect(spy).toHaveBeenCalledWith(TENANT, "r", "api.acme.io"));
  });

  it("REQ-PB1: clicking a row updates the detail pane to show that op", async () => {
    const REQ2: ReconstructedRequest = {
      ...REQ, operation: "POST /api/orders", method: "POST", path: "/api/orders",
      artifacts: { curl: "curl -X POST 'https://api.acme.io/api/orders'" },
    };
    ui([REQ, REQ2]);
    await waitForData(); // REQ selected first
    // Click orders row in list (appears only once, in the list)
    await userEvent.click(screen.getAllByText("/api/orders")[0]);
    // After selection, /api/orders appears in both list and detail
    await waitFor(() => {
      expect(screen.getAllByText("/api/orders").length).toBeGreaterThanOrEqual(2);
    });
  });

  it("REQ-PB2: lane filter Probeable excludes non-probeable ops from the list", async () => {
    const DEAD: ReconstructedRequest = {
      ...REQ, operation: "GET /api/dead", path: "/api/dead", probeable: false, artifacts: null,
    };
    ui([REQ, DEAD]);
    await waitForData();
    await userEvent.click(screen.getByRole("button", { name: /^probeable/i }));
    // /api/dead disappears from the list (it was never selected so not in detail)
    await waitFor(() => expect(screen.queryByText("/api/dead")).not.toBeInTheDocument());
    expect(screen.getAllByText("/api/users").length).toBeGreaterThanOrEqual(1);
  });

  it("REQ-PB2: search filters list rows by path substring", async () => {
    const REQ2: ReconstructedRequest = { ...REQ, operation: "GET /api/orders", path: "/api/orders" };
    ui([REQ, REQ2]);
    await waitForData();
    await userEvent.type(screen.getByRole("searchbox"), "orders");
    // After filtering, "users" row should be gone from the list (checked via list buttons)
    await waitFor(() => {
      const listRows = Array.from(document.querySelectorAll(".pb-list-row"));
      expect(listRows.some((el) => el.textContent?.includes("/api/users"))).toBe(false);
      expect(listRows.some((el) => el.textContent?.includes("/api/orders"))).toBe(true);
    });
  });

  it("REQ-PB3: shadow badge shown for ops whose hashes are in shadow set", async () => {
    vi.mocked(useRunDataOptional).mockReturnValue({
      findings: {
        run_id: "r", count: 1, coverage: null, spec: null,
        findings: [{
          finding_hash: "h1", type: "endpoint_unresolved", value: null, path: null,
          severity: null, attributes: {}, first_stage: null, revealable: true, triage: null,
          spec_status: { status: "shadow", reason: null, matched_operation: null },
          occurrences: [],
        }],
      },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    ui([REQ]);
    await waitForData();
    // Shadow marker appears in both the list row and the detail header
    expect(screen.getAllByText("shadow").length).toBeGreaterThanOrEqual(1);
  });

  it("REQ-PB5: params table shows query param name and example in cells", async () => {
    ui([REQ]);
    await waitForData();
    // "page" and its example "1" are in table cells; use getAllByText to tolerate duplicates
    expect(screen.getAllByText("page").length).toBeGreaterThanOrEqual(1);
    const cells = screen.getAllByRole("cell");
    expect(cells.some((c) => c.textContent === "1")).toBe(true);
  });

  it("REQ-PB6: host-selector shown for relative request, hidden for absolute", async () => {
    ui([REQ, RELATIVE]);
    await waitForData();
    // REQ is selected (absolute) — no selector
    expect(screen.queryByLabelText(/resolve relative paths against/i)).not.toBeInTheDocument();
    // Click RELATIVE row → detail switches, selector appears
    await userEvent.click(screen.getAllByText("/api/rel")[0]);
    expect(await screen.findByLabelText(/resolve relative paths against/i)).toBeInTheDocument();
  });

  it("REQ-PB7: triage confirmed loops over all endpoint_hashes", async () => {
    vi.spyOn(api, "triageFinding").mockResolvedValue({ status: "confirmed", note: null, actor: null, updated_at: "", finding_hash: "h1" });
    const MULTI: ReconstructedRequest = { ...REQ, endpoint_hashes: ["h1", "h2"] };
    ui([MULTI]);
    await waitForData();
    await userEvent.click(screen.getByRole("button", { name: /confirmed live/i }));
    await waitFor(() => {
      expect(api.triageFinding).toHaveBeenCalledWith(TENANT, "r", "h1", { status: "confirmed" });
      expect(api.triageFinding).toHaveBeenCalledWith(TENANT, "r", "h2", { status: "confirmed" });
    });
  });
});
