import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { TenantProvider } from "../../tenant/TenantContext";
import { DiffPage } from "./DiffPage";
import * as apiClient from "../../api/apiClient";

describe("DiffPage", () => {
  beforeEach(() => { localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });

  it("shows display values and explains an identity upgrade instead of fake churn", async () => {
    vi.spyOn(apiClient, "getRunDiff").mockResolvedValue({
      run_id: "r2", base_run_id: "r1", base_incomplete: false, identity_upgraded: true,
      new: [], gone: [],
      persisted: [{ finding_hash: "h", type: "endpoint", value: "GET /u/{id}",
        display_value: "GET /u/{userId}", path: null, severity: null, priority: 0 }],
    });
    render(<TenantProvider><DiffPage runId="r2" baseRunId="r1" /></TenantProvider>);
    expect(await screen.findByText("GET /u/{userId}")).toBeInTheDocument();
    expect(screen.getByText(/analyzed with an older finding identity/)).toBeInTheDocument();
  });
});
