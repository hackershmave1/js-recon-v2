import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BaseUrlPanel } from "./BaseUrlPanel";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type { BaseUrlRule } from "../../api/types";

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function ui() {
  vi.spyOn(api, "listBaseUrlRules").mockResolvedValue([]);
  return render(<TenantProvider><BaseUrlPanel runId="r" /></TenantProvider>);
}

const EXISTING_RULE: BaseUrlRule = {
  id: "rule-1", kind: "prefix", path_prefix: "/address", finding_hashes: [], base_url: "/location", actor: null,
};

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
    expect(screen.getByRole("button", { name: "Delete rule /address" })).toBeInTheDocument();
  });

  it("shows a readable message on a 422 invalid base", async () => {
    vi.spyOn(api, "addBaseUrlRule").mockRejectedValue(new api.ApiError(422, "invalid base_url: ..."));
    ui();
    await userEvent.type(screen.getByLabelText(/path prefix/i), "/a");
    await userEvent.type(screen.getByLabelText(/base url/i), "ftp://x");
    await userEvent.click(screen.getByRole("button", { name: /add rule/i }));
    expect(await screen.findByText(/invalid base_url/i)).toBeInTheDocument();
  });

  it("deletes a rule and removes it from the list", async () => {
    vi.spyOn(api, "listBaseUrlRules").mockResolvedValue([EXISTING_RULE]);
    vi.spyOn(api, "deleteBaseUrlRule").mockResolvedValue(undefined);
    render(<TenantProvider><BaseUrlPanel runId="r" /></TenantProvider>);
    const deleteButton = await screen.findByRole("button", { name: "Delete rule /address" });
    await userEvent.click(deleteButton);
    expect(api.deleteBaseUrlRule).toHaveBeenCalledWith(TENANT, "r", "rule-1");
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Delete rule /address" })).not.toBeInTheDocument();
    });
  });

  it("shows a readable message when delete fails and keeps the rule", async () => {
    vi.spyOn(api, "listBaseUrlRules").mockResolvedValue([EXISTING_RULE]);
    vi.spyOn(api, "deleteBaseUrlRule").mockRejectedValue(
      new api.ApiError(409, "rule is referenced by an active reclassify"),
    );
    render(<TenantProvider><BaseUrlPanel runId="r" /></TenantProvider>);
    const deleteButton = await screen.findByRole("button", { name: "Delete rule /address" });
    await userEvent.click(deleteButton);
    expect(await screen.findByText(/rule is referenced by an active reclassify/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete rule /address" })).toBeInTheDocument();
  });
});
