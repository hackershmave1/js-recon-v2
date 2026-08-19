import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProbePanel } from "./ProbePanel";
import { TenantProvider } from "../../tenant/TenantContext";
import { useRunDataOptional } from "../progress/runData";
import * as api from "../../api/apiClient";
import type { ReconstructedRequest } from "../../api/types";

// ProbePanel reads the run-data context for candidate hosts; mock it so tests control
// whether discovered hosts are present (they load best-effort, so null is a real state).
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
  return render(<TenantProvider><ProbePanel runId="r" /></TenantProvider>);
}

describe("ProbePanel", () => {
  it("lists reconstructed requests and copies curl", async () => {
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    ui([REQ]);
    expect(await screen.findByText("/api/users")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /copy curl/i }));
    expect(writeText).toHaveBeenCalledWith(REQ.artifacts!.curl);
  });

  it("copies the raw HTTP request and shows a copied tick", async () => {
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    ui([REQ]);
    await screen.findByText("/api/users");
    await userEvent.click(screen.getByRole("button", { name: /copy raw-http/i }));
    expect(writeText).toHaveBeenCalledWith(REQ.artifacts!.http);
    expect(await screen.findByRole("button", { name: /copied/i })).toBeInTheDocument();
  });

  it("shows an error when the requests fetch fails", async () => {
    vi.spyOn(api, "getRequests").mockRejectedValue(new api.ApiError(404, "run not found"));
    render(<TenantProvider><ProbePanel runId="r" /></TenantProvider>);
    expect(await screen.findByText(/run not found/i)).toBeInTheDocument();
  });

  it("marks a non-probeable request", async () => {
    ui([{ ...REQ, probeable: false, artifacts: null }]);
    expect(await screen.findByText(/not probeable/i)).toBeInTheDocument();
  });

  it("shows an empty message when there are no requests", async () => {
    ui([]);
    expect(await screen.findByText(/no probeable requests/i)).toBeInTheDocument();
  });

  it("hides the host-selector when every request already has a host", async () => {
    ui([REQ]); // absolute example_url -> not relative -> no selector
    await screen.findByText("/api/users");
    expect(screen.queryByLabelText(/resolve relative paths against/i)).not.toBeInTheDocument();
  });

  it("shows the host-selector for a relative request and re-resolves via a custom host", async () => {
    // No run-data (best-effort load absent): the selector degrades to a free-text host.
    const spy = vi.spyOn(api, "getRequests").mockResolvedValue({ run_id: "r", count: 1, requests: [RELATIVE] });
    render(<TenantProvider><ProbePanel runId="r" /></TenantProvider>);
    const select = await screen.findByLabelText(/resolve relative paths against/i);
    await userEvent.selectOptions(select, "__custom__");
    // Committed on Enter (not per keystroke) — one re-resolve for the whole host.
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
    render(<TenantProvider><ProbePanel runId="r" /></TenantProvider>);
    // Default = the crawl target (primary, in scope).
    await waitFor(() => expect(spy).toHaveBeenCalledWith(TENANT, "r", "www.acme.io"));
    await userEvent.selectOptions(await screen.findByLabelText(/resolve relative paths against/i), "api.acme.io");
    await waitFor(() => expect(spy).toHaveBeenCalledWith(TENANT, "r", "api.acme.io"));
  });
});
