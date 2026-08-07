import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TenantProvider, useTenant } from "./TenantContext";
import { TenantGate, isValidTenant } from "./TenantGate";

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllEnvs());

describe("tenant", () => {
  it("accepts server-canonicalizable UUID forms and rejects junk", () => {
    expect(isValidTenant("123e4567-e89b-12d3-a456-426614174000")).toBe(true);
    expect(isValidTenant("123e4567e89b12d3a456426614174000")).toBe(true); // un-hyphenated
    expect(isValidTenant("not-a-uuid")).toBe(false);
    expect(isValidTenant("{123e4567-e89b-12d3-a456-426614174000}")).toBe(true);       // braced
    expect(isValidTenant("urn:uuid:123e4567-e89b-12d3-a456-426614174000")).toBe(true); // urn:uuid:
    expect(isValidTenant("uuid:123e4567e89b12d3a456426614174000")).toBe(true);          // uuid: without urn: (server accepts)
    expect(isValidTenant("12{3e4567e89b12d3a456426614174000}")).toBe(false);            // mid-string brace rejected
  });

  it("gate blocks until a valid tenant is entered, then persists it", async () => {
    render(<TenantProvider><TenantGate><div>WORKSPACE</div></TenantGate></TenantProvider>);
    expect(screen.queryByText("WORKSPACE")).toBeNull();
    await userEvent.type(screen.getByLabelText(/tenant/i), "123e4567-e89b-12d3-a456-426614174000");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(screen.getByText("WORKSPACE")).toBeInTheDocument();
    expect(localStorage.getItem("recon.tenantId")).toContain("123e4567");
  });

  it("uses VITE_DEFAULT_TENANT_ID as a cold-start default when nothing is persisted", () => {
    vi.stubEnv("VITE_DEFAULT_TENANT_ID", "123e4567-e89b-12d3-a456-426614174000");
    render(<TenantProvider><TenantGate><div>WORKSPACE</div></TenantGate></TenantProvider>);
    expect(screen.getByText("WORKSPACE")).toBeInTheDocument();
    // Silent pass-through must NOT persist: the default tracks the build and can
    // never freeze a stale tenant into localStorage.
    expect(localStorage.getItem("recon.tenantId")).toBeNull();
  });

  it("persisted last-used tenant wins over the default (effective value, not just storage)", () => {
    localStorage.setItem("recon.tenantId", "99999999-9999-4999-8999-999999999999");
    vi.stubEnv("VITE_DEFAULT_TENANT_ID", "123e4567-e89b-12d3-a456-426614174000");
    function ShowTenant() {
      return <span>tenant:{useTenant().tenantId}</span>;
    }
    render(<TenantProvider><ShowTenant /></TenantProvider>);
    // Assert the EFFECTIVE active tenant (not merely that localStorage is unclobbered) so
    // the `localStorage ?? default` precedence is locked in: a reversed order would
    // surface the build default here and fail this test.
    expect(screen.getByText(/tenant:99999999-9999-4999-8999-999999999999/)).toBeInTheDocument();
    expect(screen.queryByText(/123e4567/)).toBeNull();
  });

  it("an invalid default still blocks the gate (fail-safe)", () => {
    vi.stubEnv("VITE_DEFAULT_TENANT_ID", "not-a-uuid");
    render(<TenantProvider><TenantGate><div>WORKSPACE</div></TenantGate></TenantProvider>);
    expect(screen.queryByText("WORKSPACE")).toBeNull();
  });
});
