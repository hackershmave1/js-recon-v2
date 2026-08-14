import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { TenantProvider, useTenant } from "./TenantContext";

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllEnvs());

function ShowTenant() {
  return <span>tenant:{useTenant().tenantId ?? "none"}</span>;
}

describe("tenant provider", () => {
  it("seeds the active tenant from localStorage recon.tenantId (set by login)", () => {
    localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000");
    render(
      <TenantProvider>
        <ShowTenant />
      </TenantProvider>,
    );
    expect(screen.getByText(/tenant:123e4567-e89b-12d3-a456-426614174000/)).toBeInTheDocument();
  });

  it("persisted last-used tenant wins over the build-time default", () => {
    localStorage.setItem("recon.tenantId", "99999999-9999-4999-8999-999999999999");
    vi.stubEnv("VITE_DEFAULT_TENANT_ID", "123e4567-e89b-12d3-a456-426614174000");
    render(
      <TenantProvider>
        <ShowTenant />
      </TenantProvider>,
    );
    // Assert the EFFECTIVE active tenant so the `localStorage ?? default` precedence is
    // locked in: a reversed order would surface the build default here and fail.
    expect(screen.getByText(/tenant:99999999-9999-4999-8999-999999999999/)).toBeInTheDocument();
    expect(screen.queryByText(/123e4567/)).toBeNull();
  });

  it("falls back to VITE_DEFAULT_TENANT_ID when nothing is persisted, without freezing it", () => {
    vi.stubEnv("VITE_DEFAULT_TENANT_ID", "123e4567-e89b-12d3-a456-426614174000");
    render(
      <TenantProvider>
        <ShowTenant />
      </TenantProvider>,
    );
    expect(screen.getByText(/tenant:123e4567/)).toBeInTheDocument();
    // Read, never persisted: the default tracks the build and can't freeze a stale tenant.
    expect(localStorage.getItem("recon.tenantId")).toBeNull();
  });
});
