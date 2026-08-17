import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { TenantProvider } from "./tenant/TenantContext";
import { AuthProvider } from "./auth/AuthProvider";
import { Home, RunWorkspace, OverviewRoute, SourcesRoute, FindingsRoute, ApiSpecRoute, ProbeRoute } from "./app";
import * as api from "./api/apiClient";
import * as sse from "./api/sseClient";

// A terminal run carrying one endpoint finding. The RunDataProvider fetches this on
// the SSE onOpen exactly as in the app; each page then reads it from context. Mocking
// the api/sse layer (not RunProgress) exercises the real routing + data wiring.
const DONE_STATUS = { run_id: "r1", state: "done", stage: null, done: 1, total: 1, pct: 100, eta_seconds: null, heartbeat_at: null, stalled: false, pause_requested: false, cancel_requested: false };
const FINDINGS = {
  run_id: "r1", count: 1, coverage: null, spec: null,
  findings: [{ finding_hash: "h1", type: "endpoint", value: "/api/x", path: null, severity: null, attributes: {}, first_stage: "analyze", revealable: false, triage: null, spec_status: null, occurrences: [] }],
};

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000");
  vi.spyOn(sse, "streamRunEvents").mockImplementation(async (_r, _t, h) => { h.onOpen?.(); });
  vi.spyOn(api, "getStatus").mockResolvedValue(DONE_STATUS);
  vi.spyOn(api, "getFindings").mockResolvedValue(FINDINGS);
  vi.spyOn(api, "getAssets").mockResolvedValue({ domain: null, status: "pending", assets: [] });
  vi.spyOn(api, "getRequests").mockResolvedValue({ run_id: "r1", count: 0, requests: [] });
  vi.spyOn(api, "getSources").mockResolvedValue({ run_id: "r1", count: 0, sources: [] });
});

const ROUTES = [
  { path: "/", Component: Home },
  {
    path: "/runs/:id", Component: RunWorkspace, children: [
      { index: true, Component: OverviewRoute },
      { path: "sources", Component: SourcesRoute },
      { path: "findings", Component: FindingsRoute },
      { path: "api-spec", Component: ApiSpecRoute },
      { path: "probe", Component: ProbeRoute },
    ],
  },
];

function renderAt(path: string) {
  const router = createMemoryRouter(ROUTES, { initialEntries: [path] });
  render(
    <AuthProvider>
      <TenantProvider>
        <RouterProvider router={router} />
      </TenantProvider>
    </AuthProvider>,
  );
}

describe("app routes", () => {
  it("renders the new-run panel at /", () => {
    renderAt("/");
    expect(screen.getByText(/new recon run/i)).toBeInTheDocument();
  });

  it("renders the run overview (metrics + live pipeline) at the index route", async () => {
    renderAt("/runs/r1");
    expect(await screen.findByText("DONE")).toBeInTheDocument();     // RunProgress pipeline card
    expect(screen.getByText("Endpoints")).toBeInTheDocument();       // OverviewPanel metric tile
  });

  it("surfaces the run's crawl target host in the sidebar", async () => {
    vi.spyOn(api, "getAssets").mockResolvedValue({ domain: "http://recon-range.test/", status: "ok", assets: [] });
    renderAt("/runs/r1");
    expect(await screen.findByText("recon-range.test")).toBeInTheDocument(); // Sidebar current-run card, host of the manifest domain
  });

  it("renders findings on the findings page", async () => {
    renderAt("/runs/r1/findings");
    expect(await screen.findByRole("heading", { name: "Findings" })).toBeInTheDocument(); // FindingsPage rendered
    expect(screen.getAllByText("API").length).toBeGreaterThan(0);                         // endpoint finding surfaced, labelled "API" (facet + row)
  });

  it("shows the Export spec button on the api-spec page once terminal", async () => {
    renderAt("/runs/r1/api-spec");
    expect(await screen.findByRole("button", { name: /export spec/i })).toBeInTheDocument();
  });

  it("shows the manual-probe panel on the probe page once terminal", async () => {
    renderAt("/runs/r1/probe");
    expect(await screen.findByText(/no probeable requests/i)).toBeInTheDocument();
  });
});
