import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SpecUpload } from "./SpecUpload";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import { ApiError } from "../../api/apiClient";
import type { SpecSummary } from "../../api/types";

beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });

function ui() {
  return render(<TenantProvider><SpecUpload runId="r" /></TenantProvider>);
}

const SUMMARY: SpecSummary = { documented: 2, shadow: 1, unresolved: 0, suffix_verify: 0, base_url_incompleteness_ratio: 0 };

describe("SpecUpload", () => {
  it("defaults to upload mode and gates submit until a file is chosen", async () => {
    ui();
    expect(screen.getByRole("radio", { name: /upload a file/i })).toBeChecked();
    const submit = screen.getByRole("button", { name: /attach spec/i });
    expect(submit).toBeDisabled();
    await userEvent.upload(screen.getByLabelText(/spec file/i),
      new File(["openapi: 3.0.0"], "spec.yaml", { type: "application/yaml" }));
    expect(submit).toBeEnabled();
  });

  it("posts the uploaded file to /runs/:id/spec and shows the returned bucket summary", async () => {
    vi.spyOn(api, "attachSpec").mockResolvedValue(SUMMARY);
    ui();
    await userEvent.upload(screen.getByLabelText(/spec file/i),
      new File(["openapi: 3.0.0"], "spec.yaml", { type: "application/yaml" }));
    await userEvent.click(screen.getByRole("button", { name: /attach spec/i }));

    expect(api.attachSpec).toHaveBeenCalledWith(
      "123e4567-e89b-12d3-a456-426614174000", "r", expect.any(File),
    );
    expect(await screen.findByText(/documented 2 · shadow 1 · unresolved 0/)).toBeInTheDocument();
  });

  it("switching to paste mode shows a spec-text box and posts the pasted text", async () => {
    vi.spyOn(api, "attachSpec").mockResolvedValue(SUMMARY);
    ui();
    await userEvent.click(screen.getByRole("radio", { name: /paste spec text/i }));
    expect(screen.queryByLabelText(/spec file/i)).not.toBeInTheDocument();
    const submit = screen.getByRole("button", { name: /attach spec/i });
    expect(submit).toBeDisabled();

    // Exact match, not a regex: "Paste spec text" (the radio's own label) also
    // contains the substring "spec text" and would otherwise match too.
    await userEvent.type(screen.getByLabelText("Spec text"), "openapi: 3.0.0");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);

    expect(api.attachSpec).toHaveBeenCalledWith(
      "123e4567-e89b-12d3-a456-426614174000", "r", "openapi: 3.0.0",
    );
  });

  it("shows a readable message on a 422 invalid spec", async () => {
    vi.spyOn(api, "attachSpec").mockRejectedValue(new ApiError(422, "invalid spec: not a mapping"));
    ui();
    await userEvent.upload(screen.getByLabelText(/spec file/i), new File(["not a spec"], "spec.json"));
    await userEvent.click(screen.getByRole("button", { name: /attach spec/i }));
    expect(await screen.findByText(/invalid spec/i)).toBeInTheDocument();
  });

  it("shows a readable message on a 404 run not found", async () => {
    vi.spyOn(api, "attachSpec").mockRejectedValue(new ApiError(404, "run not found"));
    ui();
    await userEvent.upload(screen.getByLabelText(/spec file/i), new File(["x"], "spec.json"));
    await userEvent.click(screen.getByRole("button", { name: /attach spec/i }));
    expect(await screen.findByText(/run not found/i)).toBeInTheDocument();
  });

  it("shows the run's already-attached summary from initialSummary, with no upload needed", () => {
    render(<TenantProvider><SpecUpload runId="r" initialSummary={SUMMARY} /></TenantProvider>);
    expect(screen.getByText(/documented 2 · shadow 1 · unresolved 0/)).toBeInTheDocument();
  });
});
