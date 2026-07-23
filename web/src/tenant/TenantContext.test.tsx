import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TenantProvider } from "./TenantContext";
import { TenantGate, isValidTenant } from "./TenantGate";

beforeEach(() => localStorage.clear());

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
});
