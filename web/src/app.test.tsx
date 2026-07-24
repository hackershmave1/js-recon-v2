import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { createMemoryRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { TenantProvider } from "./tenant/TenantContext";
import { Home, RunWorkspace } from "./app";
import type { FindingsResponse } from "./api/types";

// Mock RunProgress: no streaming/fetching. Feed findings up via an effect (NOT during
// render) so RunWorkspace's onFindings->FindingsView wiring is exercised without a
// setState-in-render warning. NOTE: define the fixture INLINE here — a top-level const
// would be in the TDZ when this hoisted factory runs.
vi.mock("./features/progress/RunProgress", () => ({
  RunProgress: ({ onFindings }: { runId: string; onFindings: (f: FindingsResponse) => void }) => {
    useEffect(() => {
      onFindings({
        run_id: "r1", count: 1, coverage: null,
        findings: [{ finding_hash: "h1", type: "endpoint", value: "/api/x", path: null, severity: "info", attributes: {}, first_stage: "analyze", revealable: false, triage: null, occurrences: [] }],
      });
    }, [onFindings]);
    return <div>PROGRESS</div>;
  },
}));

beforeEach(() => localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"));

function renderAt(path: string) {
  const router = createMemoryRouter(
    [{ path: "/", Component: Home }, { path: "/runs/:id", Component: RunWorkspace }],
    { initialEntries: [path] },
  );
  render(<TenantProvider><RouterProvider router={router} /></TenantProvider>);
}

describe("app routes", () => {
  it("renders the new-run panel at /", () => {
    renderAt("/");
    expect(screen.getByText(/new recon run/i)).toBeInTheDocument();
  });

  it("renders RunWorkspace at /runs/:id and surfaces findings via onFindings", async () => {
    renderAt("/runs/r1");
    expect(screen.getByText("PROGRESS")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /coverage/i })).toBeInTheDocument(); // FindingsView rendered
    expect(screen.getAllByText("endpoint").length).toBeGreaterThan(0);      // grouped finding rendered
  });
});
