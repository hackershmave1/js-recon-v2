import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { NewRunPanel, addScopeHost } from "./NewRunPanel";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";

const navigate = vi.fn();
vi.mock("react-router", async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

const TENANT = "123e4567-e89b-12d3-a456-426614174000";
beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", TENANT); });

function renderPanel() {
  return render(<MemoryRouter><TenantProvider><NewRunPanel /></TenantProvider></MemoryRouter>);
}

describe("NewRunPanel", () => {
  it("gates submit on authorized-by and a file — scope is optional (S4)", async () => {
    renderPanel();
    const submit = screen.getByRole("button", { name: /analyze/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester");
    expect(submit).toBeDisabled(); // still disabled: file not chosen yet
    await userEvent.upload(screen.getByLabelText(/javascript file/i),
      new File(["console.log(1)"], "app.js", { type: "text/javascript" }));
    expect(submit).toBeEnabled(); // enabled WITHOUT a scope host
  });

  it("creates a session then uploads, then navigates to the run", async () => {
    vi.spyOn(api, "createSession").mockResolvedValue({ session_id: "s1", scope_hosts: ["example.com"], authorization_ack: true });
    vi.spyOn(api, "uploadRun").mockResolvedValue({ run_id: "run-9", state: "queued" });
    renderPanel();
    // A typed-but-not-added host is still folded into the scope on submit.
    await userEvent.type(screen.getByLabelText(/scope host/i), "example.com");
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester");
    await userEvent.upload(screen.getByLabelText(/javascript file/i),
      new File(["x"], "app.js", { type: "text/javascript" }));
    await userEvent.click(screen.getByRole("button", { name: /analyze/i }));
    expect(api.createSession).toHaveBeenCalledWith(TENANT,
      { scope_hosts: ["example.com"], authorized_by: "tester" }); // no target for an upload
    expect(api.uploadRun).toHaveBeenCalled();
    const form = vi.mocked(api.uploadRun).mock.calls[0][1];
    expect(form.get("session_id")).toBe("s1");
    expect(form.get("file")).toBeInstanceOf(File);
    expect(navigate).toHaveBeenCalledWith("/runs/run-9");
  });

  it("defaults to upload mode", () => {
    renderPanel();
    expect(screen.getByRole("radio", { name: /upload/i })).toBeChecked();
    expect(screen.getByLabelText(/javascript file/i)).toBeInTheDocument();
  });

  it("crawl mode gates submit on the domain, not on scope", async () => {
    renderPanel();
    await userEvent.click(screen.getByRole("radio", { name: /crawl/i }));
    expect(screen.queryByLabelText(/javascript file/i)).not.toBeInTheDocument();
    const submit = screen.getByRole("button", { name: /crawl/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester");
    expect(submit).toBeDisabled(); // still disabled: no domain yet
    await userEvent.type(screen.getByLabelText("Domain"), "acme.io");
    expect(submit).toBeEnabled(); // enabled without a scope host — backend infers it
  });

  it("crawl with a blank scope sends only the target (backend infers scope)", async () => {
    vi.spyOn(api, "createSession").mockResolvedValue({ session_id: "s1", scope_hosts: ["acme.io"], authorization_ack: true });
    vi.spyOn(api, "startRun").mockResolvedValue({ run_id: "run-42", state: "queued" });
    renderPanel();
    await userEvent.click(screen.getByRole("radio", { name: /crawl/i }));
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester");
    await userEvent.type(screen.getByLabelText("Domain"), "acme.io");
    await userEvent.click(screen.getByRole("button", { name: /crawl/i }));
    expect(api.createSession).toHaveBeenCalledWith(TENANT,
      { scope_hosts: [], authorized_by: "tester", target: "acme.io" });
    expect(api.startRun).toHaveBeenCalledWith(TENANT, { session_id: "s1", target: "acme.io" });
    expect(navigate).toHaveBeenCalledWith("/runs/run-42");
  });

  it("crawl with runtime capture checked sends capture:true", async () => {
    vi.spyOn(api, "createSession").mockResolvedValue({ session_id: "s1", scope_hosts: ["acme.io"], authorization_ack: true });
    vi.spyOn(api, "startRun").mockResolvedValue({ run_id: "run-cap", state: "queued" });
    renderPanel();
    await userEvent.click(screen.getByRole("radio", { name: /crawl/i }));
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester");
    await userEvent.type(screen.getByLabelText("Domain"), "acme.io");
    await userEvent.click(screen.getByRole("checkbox", { name: /runtime capture/i }));
    await userEvent.click(screen.getByRole("button", { name: /crawl/i }));
    expect(api.startRun).toHaveBeenCalledWith(TENANT, { session_id: "s1", target: "acme.io", capture: true });
  });

  it("crawl with 'scan for suspected secrets' checked sends scan_suspected:true", async () => {
    // D33-B: the opt-in flows to startRun only when checked (default body stays lean).
    vi.spyOn(api, "createSession").mockResolvedValue({ session_id: "s1", scope_hosts: ["acme.io"], authorization_ack: true });
    vi.spyOn(api, "startRun").mockResolvedValue({ run_id: "run-sus", state: "queued" });
    renderPanel();
    await userEvent.click(screen.getByRole("radio", { name: /crawl/i }));
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester");
    await userEvent.type(screen.getByLabelText("Domain"), "acme.io");
    await userEvent.click(screen.getByRole("checkbox", { name: /suspected secrets/i }));
    await userEvent.click(screen.getByRole("button", { name: /crawl/i }));
    expect(api.startRun).toHaveBeenCalledWith(TENANT, { session_id: "s1", target: "acme.io", scan_suspected: true });
  });

  it("adds a scope host on Enter without submitting the form", async () => {
    renderPanel();
    await userEvent.click(screen.getByRole("radio", { name: /crawl/i }));
    const scope = screen.getByLabelText(/scope host/i);
    await userEvent.type(scope, "cdn.acme.io{Enter}");
    expect(screen.getByText("cdn.acme.io")).toBeInTheDocument(); // added as a chip
    expect(scope).toHaveValue(""); // input cleared, and the Enter did not submit
  });

  it("adds multiple scope hosts as removable chips and sends them all", async () => {
    vi.spyOn(api, "createSession").mockResolvedValue({ session_id: "s1", scope_hosts: [], authorization_ack: true });
    vi.spyOn(api, "startRun").mockResolvedValue({ run_id: "run-7", state: "queued" });
    renderPanel();
    await userEvent.click(screen.getByRole("radio", { name: /crawl/i }));
    await userEvent.type(screen.getByLabelText(/authorized by/i), "tester");
    await userEvent.type(screen.getByLabelText("Domain"), "acme.io");
    const scope = screen.getByLabelText(/scope host/i);
    await userEvent.type(scope, "cdn.acme.io");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));
    await userEvent.type(scope, "static.acme.io");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));
    expect(screen.getByText("cdn.acme.io")).toBeInTheDocument();
    // Remove one chip, keep the other.
    await userEvent.click(screen.getByRole("button", { name: /remove cdn\.acme\.io/i }));
    expect(screen.queryByText("cdn.acme.io")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /crawl/i }));
    expect(api.createSession).toHaveBeenCalledWith(TENANT,
      { scope_hosts: ["static.acme.io"], authorized_by: "tester", target: "acme.io" });
  });
});

describe("addScopeHost (mirrors the backend's *.host -> host fold)", () => {
  it("folds a wildcard to its bare host — equivalent scope, and what the backend stores", () => {
    expect(addScopeHost([], "*.acme.io")).toEqual(["acme.io"]);
  });

  it("drops a wildcard whose bare host is already in scope", () => {
    expect(addScopeHost(["acme.io"], "*.acme.io")).toEqual(["acme.io"]);
  });

  it("keeps distinct hosts and ignores exact duplicates", () => {
    expect(addScopeHost(["cdn.acme.io"], "static.acme.io")).toEqual(["cdn.acme.io", "static.acme.io"]);
    expect(addScopeHost(["acme.io"], "acme.io")).toEqual(["acme.io"]);
  });

  it("drops empty / lone-'*.' input instead of adding a junk chip", () => {
    expect(addScopeHost(["acme.io"], "   ")).toEqual(["acme.io"]);
    expect(addScopeHost(["acme.io"], "*.")).toEqual(["acme.io"]);
  });
});
