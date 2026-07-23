import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { NewRunPanel } from "./NewRunPanel";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";

const navigate = vi.fn();
vi.mock("react-router", async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });

function renderPanel() {
  return render(<MemoryRouter><TenantProvider><NewRunPanel /></TenantProvider></MemoryRouter>);
}

describe("NewRunPanel", () => {
  it("gates submit until scope host, authorized-by, and a file are provided", async () => {
    renderPanel();
    const submit = screen.getByRole("button", { name: /analyze/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/scope host/i), "example.com");
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester");
    expect(submit).toBeDisabled(); // still disabled: file not chosen yet
    await userEvent.upload(screen.getByLabelText(/javascript file/i),
      new File(["console.log(1)"], "app.js", { type: "text/javascript" }));
    expect(submit).toBeEnabled();
  });

  it("creates a session then uploads, then navigates to the run", async () => {
    vi.spyOn(api, "createSession").mockResolvedValue({ session_id: "s1", scope_hosts: ["example.com"], authorization_ack: true });
    vi.spyOn(api, "uploadRun").mockResolvedValue({ run_id: "run-9", state: "queued" });
    renderPanel();
    await userEvent.type(screen.getByLabelText(/scope host/i), "example.com");
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester");
    await userEvent.upload(screen.getByLabelText(/javascript file/i),
      new File(["x"], "app.js", { type: "text/javascript" }));
    await userEvent.click(screen.getByRole("button", { name: /analyze/i }));
    expect(api.createSession).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174000",
      { scope_hosts: ["example.com"], authorized_by: "tester" });
    expect(api.uploadRun).toHaveBeenCalled();
    const form = vi.mocked(api.uploadRun).mock.calls[0][1];
    expect(form.get("session_id")).toBe("s1");
    expect(form.get("file")).toBeInstanceOf(File);
    expect(navigate).toHaveBeenCalledWith("/runs/run-9");
  });
});
