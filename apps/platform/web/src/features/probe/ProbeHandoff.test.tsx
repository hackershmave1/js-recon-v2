import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";
import { ProbeHandoff } from "./ProbeHandoff";
import * as apiClient from "../../api/apiClient";
import { TenantProvider } from "../../tenant/TenantContext";
import type { ReconstructedRequest } from "../../api/types";

vi.mock("../../api/apiClient", () => ({
  triageFinding: vi.fn().mockResolvedValue(undefined),
  ApiError: class ApiError extends Error {},
}));

function makeReq(hashes: string[] = ["h1", "h2"]): ReconstructedRequest {
  return {
    operation: "op1", method: "GET", path: "/api/items", hosts: ["api.example.com"],
    query_params: [], body_params: [], content_type: null,
    example_url: "https://api.example.com/api/items",
    probeable: true, endpoint_hashes: hashes, artifacts: null,
  };
}

function setup(hashes = ["h1", "h2"], triageStatus: string | null = null) {
  const onTriaged = vi.fn();
  render(
    <TenantProvider>
      <MemoryRouter>
        <ProbeHandoff
          req={makeReq(hashes)}
          runId="run1"
          triageStatus={triageStatus}
          onTriaged={onTriaged}
        />
      </MemoryRouter>
    </TenantProvider>,
  );
  return { onTriaged };
}

describe("ProbeHandoff", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("recon.tenantId", "t1");
  });

  it("shows the ADR-0006 no-automated-traffic disclaimer", () => {
    setup([]);
    expect(screen.getByText(/no automated traffic/i)).toBeTruthy();
  });

  it("hides triage buttons when endpoint_hashes is empty", () => {
    setup([]);
    expect(screen.queryByRole("button", { name: /confirmed/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /dismiss/i })).toBeNull();
  });

  it("calls triageFinding for every endpoint hash on confirm", async () => {
    const user = userEvent.setup();
    const { onTriaged } = setup(["h1", "h2"]);
    await user.click(screen.getByRole("button", { name: /confirmed live/i }));
    await waitFor(() => {
      expect(apiClient.triageFinding).toHaveBeenCalledWith("t1", "run1", "h1", { status: "confirmed" });
      expect(apiClient.triageFinding).toHaveBeenCalledWith("t1", "run1", "h2", { status: "confirmed" });
    });
    expect(onTriaged).toHaveBeenCalledWith(["h1", "h2"], "confirmed");
  });

  it("calls triageFinding for every hash on dismiss", async () => {
    const user = userEvent.setup();
    const { onTriaged } = setup(["h1"]);
    await user.click(screen.getByRole("button", { name: /dismiss/i }));
    await waitFor(() => {
      expect(apiClient.triageFinding).toHaveBeenCalledWith("t1", "run1", "h1", { status: "dismissed" });
    });
    expect(onTriaged).toHaveBeenCalledWith(["h1"], "dismissed");
  });

  it("disables confirmed button when already confirmed", () => {
    setup(["h1"], "confirmed");
    const btn = screen.getByRole("button", { name: /confirmed/i });
    expect(btn).toBeDisabled();
  });

  it("disables dismissed button when already dismissed", () => {
    setup(["h1"], "dismissed");
    const btn = screen.getByRole("button", { name: /dismissed/i });
    expect(btn).toBeDisabled();
  });

  it("shows confirmed checkmark text when status is confirmed", () => {
    setup(["h1"], "confirmed");
    expect(screen.getByText("Confirmed ✓")).toBeTruthy();
  });

  it("jump to source button navigates to sources", async () => {
    const user = userEvent.setup();
    let navigated = "";
    render(
      <TenantProvider>
        <MemoryRouter initialEntries={["/probe"]}>
          <Routes>
            <Route
              path="/probe"
              element={
                <ProbeHandoff
                  req={makeReq(["h1"])}
                  runId="run42"
                  triageStatus={null}
                  onTriaged={vi.fn()}
                />
              }
            />
            <Route
              path="/runs/:id/sources"
              element={<div ref={(el) => { if (el) navigated = "sources"; }} />}
            />
          </Routes>
        </MemoryRouter>
      </TenantProvider>,
    );
    await user.click(screen.getByRole("button", { name: /jump to source/i }));
    await waitFor(() => expect(navigated).toBe("sources"));
  });
});
