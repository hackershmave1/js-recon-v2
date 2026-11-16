import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { AuthProvider } from "./AuthProvider";
import { AuthGate } from "./AuthGate";
import * as api from "../api/apiClient";
import { AUTH_TOKEN_KEY, ApiError } from "../api/apiClient";
import { captureHandoffFragment, resetHandoffForTests } from "./handoffFragment";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
const CODE = "B".repeat(43);
const RESULT = { token: "new.tok", user: "researcher", role: "analyst", tenant: { id: TENANT, name: "Acme" } };

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  resetHandoffForTests();
  window.history.replaceState(null, "", "/");
});

function land(code = CODE) {
  window.history.replaceState(null, "", `/#handoff=${code}`);
  captureHandoffFragment();
}

function renderGated(strict = false) {
  const tree = (
    <AuthProvider>
      <AuthGate>
        <div>WORKSPACE</div>
      </AuthGate>
    </AuthProvider>
  );
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}

function fakeToken(exp: number): string {
  const payload = btoa(JSON.stringify({ typ: "auth", t: TENANT, role: "admin", exp }))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${payload}.sig`;
}

describe("handoff sign-in", () => {
  it("asks for one click, then stores the token and shows the app", async () => {
    const spy = vi.spyOn(api, "exchangeHandoffCode").mockResolvedValue(RESULT);
    land();
    renderGated(true); // StrictMode: the code must still be spent exactly once
    expect(await screen.findByRole("button", { name: /continue as researcher/i })).toBeInTheDocument();
    expect(screen.getByText(/Acme/)).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(AUTH_TOKEN_KEY)).toBeNull(); // nothing stored before the click
    expect(screen.queryByText("WORKSPACE")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /continue as researcher/i }));
    expect(screen.getByText("WORKSPACE")).toBeInTheDocument();
    expect(localStorage.getItem(AUTH_TOKEN_KEY)).toBe("new.tok");
    expect(localStorage.getItem("recon.tenantId")).toBe(TENANT);
  });

  it("cancel discards the session and falls back to the login screen", async () => {
    vi.spyOn(api, "exchangeHandoffCode").mockResolvedValue(RESULT);
    land();
    renderGated();
    await userEvent.click(await screen.findByRole("button", { name: /not me|cancel|sign in with a password/i }));
    expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    expect(localStorage.getItem(AUTH_TOKEN_KEY)).toBeNull();
  });

  it("a failed exchange shows the plain login screen and no error text", async () => {
    vi.spyOn(api, "exchangeHandoffCode").mockRejectedValue(new ApiError(401, "invalid or expired code"));
    land();
    renderGated();
    expect(await screen.findByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByText(/invalid or expired|handoff|code/i)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("names the account you are switching from", async () => {
    localStorage.setItem(AUTH_TOKEN_KEY, fakeToken(Math.floor(Date.now() / 1000) + 3600));
    localStorage.setItem("recon.authUser", "someone-else");
    vi.spyOn(api, "getMe").mockResolvedValue({ user_id: "x", role: "admin", tenant: { id: TENANT, name: "Old" } });
    vi.spyOn(api, "exchangeHandoffCode").mockResolvedValue(RESULT);
    land();
    renderGated();
    expect(await screen.findByText(/signed in as someone-else/i)).toBeInTheDocument();
  });

  it("does not validate a stored token (getMe) before the handoff settles", async () => {
    localStorage.setItem(AUTH_TOKEN_KEY, fakeToken(Math.floor(Date.now() / 1000) + 3600));
    const getMe = vi.spyOn(api, "getMe").mockRejectedValue(new ApiError(401, "stale"));
    let release: (v: typeof RESULT) => void = () => {};
    vi.spyOn(api, "exchangeHandoffCode").mockReturnValue(new Promise((r) => (release = r)));
    land();
    renderGated();
    expect(screen.queryByRole("heading", { name: /sign in/i })).toBeNull(); // no login flash
    expect(getMe).not.toHaveBeenCalled();
    release(RESULT);
    expect(await screen.findByRole("button", { name: /continue as researcher/i })).toBeInTheDocument();
  });

  it("without a handoff the gate behaves as before", () => {
    renderGated();
    expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
  });
});
