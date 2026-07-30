import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProbePanel } from "./ProbePanel";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type { ReconstructedRequest } from "../../api/types";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

const REQ: ReconstructedRequest = {
  operation: "GET /api/users", method: "GET", path: "/api/users", hosts: ["api.acme.io"],
  query_params: [{ name: "page", example: "1" }], body_params: [], content_type: null,
  example_url: "https://api.acme.io/api/users?page=1", probeable: true, endpoint_hashes: ["h1"],
  artifacts: { curl: "curl 'https://api.acme.io/api/users?page=1'", http: "GET /api/users?page=1 HTTP/1.1" },
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
});
