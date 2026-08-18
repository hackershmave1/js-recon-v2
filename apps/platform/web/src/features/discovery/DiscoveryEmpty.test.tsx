import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { DiscoveryEmpty } from "./DiscoveryEmpty";
import { TenantProvider } from "../../tenant/TenantContext";
import * as api from "../../api/apiClient";
import type { AssetsManifest } from "../../api/types";

const navigate = vi.fn();
vi.mock("react-router", async (orig) => ({ ...(await orig() as object), useNavigate: () => navigate }));

beforeEach(() => { vi.restoreAllMocks(); localStorage.setItem("recon.tenantId", "123e4567-e89b-12d3-a456-426614174000"); });

function renderEmpty(
  state: string | null,
  failure?: { category: string; reason: string; host?: string | null },
) {
  return render(
    <MemoryRouter>
      <TenantProvider>
        <DiscoveryEmpty
          runId="r1"
          state={state}
          failureCategory={failure?.category ?? null}
          failureReason={failure?.reason ?? null}
          failureHost={failure?.host ?? null}
        />
      </TenantProvider>
    </MemoryRouter>,
  );
}

const CRAWL_EMPTY: AssetsManifest = { domain: "acme.io", status: "ok", assets: [] };

describe("DiscoveryEmpty", () => {
  it("shows the empty-state for a finished crawl with zero in-scope JS", async () => {
    vi.spyOn(api, "getAssets").mockResolvedValue(CRAWL_EMPTY);
    renderEmpty("done");
    expect(await screen.findByText(/no in-scope javascript discovered/i)).toBeInTheDocument();
    expect(screen.getByText("acme.io")).toBeInTheDocument();
  });

  it("routes to a new run from the CTA", async () => {
    vi.spyOn(api, "getAssets").mockResolvedValue(CRAWL_EMPTY);
    renderEmpty("done");
    await userEvent.click(await screen.findByRole("button", { name: /start a new run/i }));
    expect(navigate).toHaveBeenCalledWith("/");
  });

  it("renders nothing (and fetches nothing) while the run is not terminal", () => {
    const spy = vi.spyOn(api, "getAssets").mockResolvedValue(CRAWL_EMPTY);
    const { container } = renderEmpty("running");
    expect(container).toBeEmptyDOMElement();
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders nothing for an upload run (no crawl domain)", async () => {
    vi.spyOn(api, "getAssets").mockResolvedValue({ domain: null, status: "pending", assets: [] });
    const { container } = renderEmpty("done");
    await waitFor(() => expect(api.getAssets).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the crawl did find assets", async () => {
    vi.spyOn(api, "getAssets").mockResolvedValue({
      domain: "acme.io", status: "ok",
      assets: [{ url: "https://acme.io/app.js", source: "katana", fetch_status: "ok", analyze_status: "ok" }],
    });
    const { container } = renderEmpty("done");
    await waitFor(() => expect(api.getAssets).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("does not show the empty-crawl card for a non-done terminal run", () => {
    // Card (b) is done-only: a partial/cancelled crawl is explained by the pipeline,
    // not mislabelled "no in-scope JavaScript discovered".
    const spy = vi.spyOn(api, "getAssets").mockResolvedValue(CRAWL_EMPTY);
    const { container } = renderEmpty("partial");
    expect(container).toBeEmptyDOMElement();
    expect(spy).not.toHaveBeenCalled();
  });

  it("shows the classified reason for a failed run without fetching the manifest", () => {
    const spy = vi.spyOn(api, "getAssets").mockResolvedValue(CRAWL_EMPTY);
    renderEmpty("failed", {
      category: "access_denied",
      reason:
        "The target refused the request (HTTP 403 Forbidden). If it is an auth-gated or " +
        "bot-protected page, capture the JavaScript from a signed-in browser session with " +
        "the capture extension.",
    });
    expect(screen.getByText(/the target refused the crawler/i)).toBeInTheDocument();
    expect(screen.getByText(/capture extension/i)).toBeInTheDocument();
    // A classified failure carries its own message — no manifest lookup needed.
    expect(spy).not.toHaveBeenCalled();
  });

  it("names the out-of-scope host in a failed run's headline", () => {
    renderEmpty("failed", {
      category: "out_of_scope",
      reason: "The crawl reached evil.example, which is outside the engagement scope.",
      host: "evil.example",
    });
    // The host appears in the headline (and again in the reason body); assert the headline.
    expect(screen.getByText(/the crawl reached evil\.example, outside your scope/i)).toBeInTheDocument();
  });
});
