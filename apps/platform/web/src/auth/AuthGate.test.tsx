import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AuthProvider } from "./AuthProvider";
import { AuthGate } from "./AuthGate";
import * as api from "../api/apiClient";
import { ApiError } from "../api/apiClient";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

function renderGated() {
  render(
    <AuthProvider>
      <AuthGate>
        <div>WORKSPACE</div>
      </AuthGate>
    </AuthProvider>,
  );
}

/** A syntactically-valid (unsigned) auth token payload for bootstrap tests. */
function fakeToken(expEpoch: number): string {
  const payload = btoa(JSON.stringify({ typ: "auth", t: TENANT, role: "admin", exp: expEpoch }))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `${payload}.sig`;
}

describe("auth gate + login", () => {
  it("shows the login screen when no token is stored", () => {
    renderGated();
    expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByText("WORKSPACE")).toBeNull();
  });

  it("logs in, stores the token + tenant, and reveals the app", async () => {
    const loginSpy = vi.spyOn(api, "login").mockResolvedValue({
      token: "tok.sig",
      user: "admin",
      role: "admin",
      tenant: { id: TENANT, name: "QA" },
    });
    renderGated();

    await userEvent.type(screen.getByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "admin");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("WORKSPACE")).toBeInTheDocument();
    expect(loginSpy).toHaveBeenCalledWith("admin", "admin");
    expect(localStorage.getItem("recon.authToken")).toBe("tok.sig");
    // The tenant is mirrored so TenantProvider (unchanged) resolves it.
    expect(localStorage.getItem("recon.tenantId")).toBe(TENANT);
  });

  it("shows an error on invalid credentials and stays on the login screen", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new ApiError(401, "invalid credentials"));
    renderGated();

    await userEvent.type(screen.getByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid username or password/i);
    expect(screen.queryByText("WORKSPACE")).toBeNull();
    expect(localStorage.getItem("recon.authToken")).toBeNull();
  });

  it("restores a session synchronously from a valid stored token", () => {
    localStorage.setItem("recon.authToken", fakeToken(Math.floor(Date.now() / 1000) + 3600));
    const meSpy = vi
      .spyOn(api, "getMe")
      .mockResolvedValue({ user_id: "u1", role: "admin", tenant: { id: TENANT, name: "QA" } });
    renderGated();

    // Synchronous bootstrap => the app is visible on first render (no null-tenant flash),
    // and the token is mirrored to recon.tenantId before children mount.
    expect(screen.getByText("WORKSPACE")).toBeInTheDocument();
    expect(localStorage.getItem("recon.tenantId")).toBe(TENANT);
    expect(meSpy).toHaveBeenCalled();
  });

  it("discards an expired stored token and shows the login screen", () => {
    localStorage.setItem("recon.authToken", fakeToken(Math.floor(Date.now() / 1000) - 10));
    renderGated();
    expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    expect(localStorage.getItem("recon.authToken")).toBeNull();
  });

  it("logs out a stored token the server rejects (401 from /me)", async () => {
    localStorage.setItem("recon.authToken", fakeToken(Math.floor(Date.now() / 1000) + 3600));
    vi.spyOn(api, "getMe").mockRejectedValue(new ApiError(401, "invalid"));
    renderGated();

    // Optimistically shows the app, then the /me 401 flips back to login and clears the token.
    expect(await screen.findByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    expect(localStorage.getItem("recon.authToken")).toBeNull();
  });
});
