import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AssetsInventory } from "./AssetsInventory";
import * as api from "../../api/apiClient";

describe("AssetsInventory", () => {
  it("lists discovered assets with the crawl status", async () => {
    vi.spyOn(api, "getAssets").mockResolvedValue({
      domain: "acme.io", status: "ok",
      assets: [{ url: "https://acme.io/app.js", source: "katana" }],
    });
    render(<AssetsInventory tenantId="t" runId="r" />);
    await waitFor(() => expect(screen.getByText("https://acme.io/app.js")).toBeInTheDocument());
    expect(screen.getByText(/1 asset/i)).toBeInTheDocument();
    expect(screen.getByText(/ok/i)).toBeInTheDocument();
  });
});
