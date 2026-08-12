import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PairDeviceButton, PairDeviceModal } from "./PairDeviceButton";
import * as tenant from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type { PairingToken } from "../../api/types";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
const TOKEN: PairingToken = { token: "payload.signature", ttlSeconds: 43200, expiresAt: 1786613064 };

beforeEach(() => vi.restoreAllMocks());

describe("PairDeviceModal", () => {
  it("mints on open and shows the pairing code + expiry", async () => {
    vi.spyOn(api, "mintPairingToken").mockResolvedValue(TOKEN);
    render(<PairDeviceModal tenantId={TENANT} onClose={vi.fn()} />);
    expect(await screen.findByDisplayValue(TOKEN.token)).toBeInTheDocument();
    expect(api.mintPairingToken).toHaveBeenCalledWith(TENANT);
    expect(screen.getByText(/expires in ~12h/i)).toBeInTheDocument();
  });

  it("shows a 'not enabled' message + retry when pairing is unconfigured (503)", async () => {
    vi.spyOn(api, "mintPairingToken").mockRejectedValue(new api.ApiError(503, "pairing is not configured"));
    render(<PairDeviceModal tenantId={TENANT} onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/RECON_PAIRING_KEY/);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("shows an 'unknown tenant' message on a 404 whose detail names the tenant", async () => {
    vi.spyOn(api, "mintPairingToken").mockRejectedValue(new api.ApiError(404, "unknown tenant"));
    render(<PairDeviceModal tenantId={TENANT} onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/isn't recognized|bootstrap/i);
  });

  it("shows a generic 'not available' message on a route-unmounted 404 (ingest disabled)", async () => {
    vi.spyOn(api, "mintPairingToken").mockRejectedValue(new api.ApiError(404, "Not Found"));
    render(<PairDeviceModal tenantId={TENANT} onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/isn't available/i);
  });

  it("shows a reach-failure message when the mint rejects with a raw network error", async () => {
    // A fetch network failure rejects with TypeError, not ApiError — the generic branch.
    vi.spyOn(api, "mintPairingToken").mockRejectedValue(new TypeError("Failed to fetch"));
    render(<PairDeviceModal tenantId={TENANT} onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't reach the server/i);
  });

  it("copies the token to the clipboard and flips to Copied", async () => {
    vi.spyOn(api, "mintPairingToken").mockResolvedValue(TOKEN);
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    render(<PairDeviceModal tenantId={TENANT} onClose={vi.fn()} />);
    await screen.findByDisplayValue(TOKEN.token);
    await userEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    expect(writeText).toHaveBeenCalledWith(TOKEN.token);
    expect(await screen.findByRole("button", { name: /copied/i })).toBeInTheDocument();
  });

  it("degrades gracefully when the clipboard API is unavailable (non-secure context)", async () => {
    vi.spyOn(api, "mintPairingToken").mockResolvedValue(TOKEN);
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    try {
      render(<PairDeviceModal tenantId={TENANT} onClose={vi.fn()} />);
      await screen.findByDisplayValue(TOKEN.token);
      await userEvent.click(screen.getByRole("button", { name: /^copy$/i }));
      expect(await screen.findByText(/copy it manually/i)).toBeInTheDocument();
    } finally {
      Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => {} }, configurable: true });
    }
  });

  it("re-mints when Try again is clicked after an error", async () => {
    const mint = vi.spyOn(api, "mintPairingToken")
      .mockRejectedValueOnce(new api.ApiError(503, "pairing is not configured"))
      .mockResolvedValueOnce(TOKEN);
    render(<PairDeviceModal tenantId={TENANT} onClose={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: /try again/i }));
    expect(await screen.findByDisplayValue(TOKEN.token)).toBeInTheDocument();
    expect(mint).toHaveBeenCalledTimes(2);
  });
});

describe("PairDeviceButton", () => {
  it("opens the pairing modal on click", async () => {
    vi.spyOn(tenant, "useTenant").mockReturnValue({ tenantId: TENANT, setTenantId: vi.fn() });
    vi.spyOn(api, "mintPairingToken").mockResolvedValue(TOKEN);
    render(<PairDeviceButton />);
    await userEvent.click(screen.getByRole("button", { name: /pair device/i }));
    expect(await screen.findByRole("dialog", { name: /pair a capture device/i })).toBeInTheDocument();
    expect(api.mintPairingToken).toHaveBeenCalledWith(TENANT);
  });

  it("renders nothing when there is no tenant (defensive null-guard)", () => {
    vi.spyOn(tenant, "useTenant").mockReturnValue({ tenantId: null, setTenantId: vi.fn() });
    const { container } = render(<PairDeviceButton />);
    expect(container).toBeEmptyDOMElement();
  });
});
