import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { NewRunPanel } from "./features/newRun/NewRunPanel";
import { TenantProvider } from "./tenant/TenantContext";
import { TenantGate } from "./tenant/TenantGate";

beforeEach(() => localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"));

describe("app routes", () => {
  it("renders the new-run panel at /", () => {
    render(
      <TenantProvider><TenantGate>
        <MemoryRouter initialEntries={["/"]}><Routes><Route path="/" element={<NewRunPanel />} /></Routes></MemoryRouter>
      </TenantGate></TenantProvider>);
    expect(screen.getByText(/new recon run/i)).toBeInTheDocument();
  });
});
