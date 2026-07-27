import { render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AssetsInventory } from "./AssetsInventory";
import * as api from "../../api/apiClient";

describe("AssetsInventory", () => {
  it("lists discovered assets with the crawl status", async () => {
    vi.spyOn(api, "getAssets").mockResolvedValue({
      domain: "acme.io", status: "ok",
      assets: [{ url: "https://acme.io/app.js", source: "katana", fetch_status: "ok", analyze_status: "ok" }],
    });
    render(<AssetsInventory tenantId="t" runId="r" />);
    await waitFor(() => expect(screen.getByText("https://acme.io/app.js")).toBeInTheDocument());
    expect(screen.getByText(/1 asset/i)).toBeInTheDocument();
    expect(screen.getByText(/crawl status: ok/i)).toBeInTheDocument();
  });

  it("shows each asset's own fetch/analyze status chips, mixed across rows (Slice Y)", async () => {
    vi.spyOn(api, "getAssets").mockResolvedValue({
      domain: "acme.io", status: "ok",
      assets: [
        { url: "https://acme.io/a.js", source: "katana", fetch_status: "ok", analyze_status: "ok" },
        { url: "https://acme.io/b.js", source: "katana", fetch_status: "failed", analyze_status: "pending" },
      ],
    });
    render(<AssetsInventory tenantId="t" runId="r" />);
    await waitFor(() => expect(screen.getByText("https://acme.io/a.js")).toBeInTheDocument());

    const rowA = screen.getByText("https://acme.io/a.js").closest("li");
    const rowB = screen.getByText("https://acme.io/b.js").closest("li");
    if (!rowA || !rowB) throw new Error("expected both asset rows to render as <li>");

    expect(within(rowA).getByText(/fetch: ok/i)).toBeInTheDocument();
    expect(within(rowA).getByText(/analyze: ok/i)).toBeInTheDocument();
    expect(within(rowB).getByText(/fetch: failed/i)).toBeInTheDocument();
    expect(within(rowB).getByText(/analyze: pending/i)).toBeInTheDocument();
  });
});
