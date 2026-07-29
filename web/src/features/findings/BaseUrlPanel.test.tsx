import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BaseUrlPanel } from "./BaseUrlPanel";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function ui() {
  vi.spyOn(api, "listBaseUrlRules").mockResolvedValue([]);
  return render(<TenantProvider><BaseUrlPanel runId="r" /></TenantProvider>);
}

describe("BaseUrlPanel", () => {
  it("posts a prefix rule and lists it", async () => {
    vi.spyOn(api, "addBaseUrlRule").mockResolvedValue({
      rule: { id: "1", kind: "prefix", path_prefix: "/address", finding_hashes: [], base_url: "/location", actor: null },
      summary: { documented: 1, shadow: 0, unresolved: 0, suffix_verify: 0, base_url_incompleteness_ratio: 0 },
    });
    ui();
    await userEvent.type(screen.getByLabelText(/path prefix/i), "/address");
    await userEvent.type(screen.getByLabelText(/base url/i), "/location");
    await userEvent.click(screen.getByRole("button", { name: /add rule/i }));
    expect(api.addBaseUrlRule).toHaveBeenCalledWith(TENANT, "r", {
      kind: "prefix", path_prefix: "/address", base_url: "/location",
    });
    expect(await screen.findByText(/documented 1/)).toBeInTheDocument();
  });

  it("shows a readable message on a 422 invalid base", async () => {
    vi.spyOn(api, "addBaseUrlRule").mockRejectedValue(new api.ApiError(422, "invalid base_url: ..."));
    ui();
    await userEvent.type(screen.getByLabelText(/path prefix/i), "/a");
    await userEvent.type(screen.getByLabelText(/base url/i), "ftp://x");
    await userEvent.click(screen.getByRole("button", { name: /add rule/i }));
    expect(await screen.findByText(/invalid base_url/i)).toBeInTheDocument();
  });
});
