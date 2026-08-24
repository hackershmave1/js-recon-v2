import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { EditRerunPanel } from "./EditRerunPanel";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type { RunConfig } from "../../api/types";

const navigate = vi.fn();
vi.mock("react-router", async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
const MIB = 1024 * 1024;
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function crawlCfg(over: Partial<RunConfig> = {}): RunConfig {
  return {
    run_id: "run-1", target: "acme.io", crawl_mode: null,
    scope_hosts: ["acme.io"], max_fetch_bytes: null, is_upload: false,
    scan_suspected_secrets: null, ...over,
  };
}

function renderPanel() {
  return render(
    <MemoryRouter><TenantProvider>
      <EditRerunPanel runId="run-1" onCancel={() => {}} />
    </TenantProvider></MemoryRouter>,
  );
}

describe("EditRerunPanel", () => {
  it("prefills target + scope, hides the ack, and re-runs with the edited body", async () => {
    vi.spyOn(api, "getRunConfig").mockResolvedValue(crawlCfg());
    vi.spyOn(api, "editAndRerun").mockResolvedValue({ run_id: "run-2", state: "queued" });
    renderPanel();
    expect(await screen.findByLabelText("Domain")).toHaveValue("acme.io");
    expect(screen.getByText("acme.io")).toBeInTheDocument(); // scope chip prefilled
    // No scope change yet => no re-attestation prompt.
    expect(screen.queryByLabelText(/authorized by/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /re-run/i }));
    expect(api.editAndRerun).toHaveBeenCalledWith(TENANT, "run-1",
      { scope_hosts: ["acme.io"], target: "acme.io", capture: false, scan_suspected: false });
    expect(navigate).toHaveBeenCalledWith("/runs/run-2");
  });

  it("requires a fresh ack when scope changes, then sends authorized_by", async () => {
    vi.spyOn(api, "getRunConfig").mockResolvedValue(crawlCfg());
    vi.spyOn(api, "editAndRerun").mockResolvedValue({ run_id: "run-3", state: "queued" });
    renderPanel();
    await screen.findByLabelText("Domain");
    await userEvent.type(screen.getByPlaceholderText(/example\.com/i), "cdn.acme.io{Enter}");
    const submit = screen.getByRole("button", { name: /re-run/i });
    expect(submit).toBeDisabled(); // MF1: a widened scope must be re-attested
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester-2");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(api.editAndRerun).toHaveBeenCalledWith(TENANT, "run-1", {
      scope_hosts: ["acme.io", "cdn.acme.io"], target: "acme.io",
      capture: false, scan_suspected: false, authorized_by: "tester-2",
    });
  });

  it("hides capture + fetch-cap for an upload source but keeps target editable", async () => {
    vi.spyOn(api, "getRunConfig").mockResolvedValue(crawlCfg({ is_upload: true }));
    renderPanel();
    expect(await screen.findByLabelText(/target/i)).toHaveValue("acme.io");
    expect(screen.queryByRole("checkbox", { name: /runtime capture/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/fetch size cap/i)).not.toBeInTheDocument();
  });

  it("sends a per-run fetch cap in bytes", async () => {
    vi.spyOn(api, "getRunConfig").mockResolvedValue(crawlCfg());
    vi.spyOn(api, "editAndRerun").mockResolvedValue({ run_id: "run-4", state: "queued" });
    renderPanel();
    await screen.findByLabelText("Domain");
    await userEvent.type(screen.getByLabelText(/fetch size cap/i), "32");
    await userEvent.click(screen.getByRole("button", { name: /re-run/i }));
    expect(api.editAndRerun).toHaveBeenCalledWith(TENANT, "run-1", {
      scope_hosts: ["acme.io"], target: "acme.io", capture: false,
      max_fetch_bytes: 32 * MIB, scan_suspected: false,
    });
  });

  it("prefills the suspected-secret toggle and sends it when re-run", async () => {
    // D33-B: a re-run of a run that had the lane on inherits the toggle (checked), and
    // toggling it flows to editAndRerun as scan_suspected.
    vi.spyOn(api, "getRunConfig").mockResolvedValue(crawlCfg({ scan_suspected_secrets: true }));
    vi.spyOn(api, "editAndRerun").mockResolvedValue({ run_id: "run-2", state: "queued" });
    renderPanel();
    const toggle = await screen.findByRole("checkbox", { name: /suspected secrets/i });
    expect(toggle).toBeChecked();  // inherited from the source run
    await userEvent.click(toggle);  // turn it off for this re-run
    await userEvent.click(screen.getByRole("button", { name: /re-run/i }));
    expect(api.editAndRerun).toHaveBeenCalledWith(
      TENANT,
      "run-1",
      expect.objectContaining({ scan_suspected: false }),
    );
  });
});
