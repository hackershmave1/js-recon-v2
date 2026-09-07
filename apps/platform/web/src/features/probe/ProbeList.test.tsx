import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ProbeList } from "./ProbeList";
import type { ReconstructedRequest } from "../../api/types";

function makeReq(op: string, overrides: Partial<ReconstructedRequest> = {}): ReconstructedRequest {
  return {
    operation: op, method: "GET", path: `/api/${op}`, hosts: ["api.example.com"],
    query_params: [], body_params: [], content_type: null,
    example_url: "https://api.example.com/api/" + op,
    probeable: true, endpoint_hashes: [op + "_hash"], artifacts: null,
    ...overrides,
  };
}

const REQUESTS: ReconstructedRequest[] = [
  makeReq("checkout", { method: "POST", path: "/api/checkout/session" }),
  makeReq("items", { method: "GET", path: "/api/items" }),
  makeReq("ws-feed", { method: "WS", path: "/ws/notifications", example_url: null }),
  makeReq("relative", { method: "GET", path: "/api/v2/search", example_url: null }),
  makeReq("no-probe", { method: "GET", path: "/api/internal", probeable: false }),
];

function setup(overrides: Partial<Parameters<typeof ProbeList>[0]> = {}) {
  const onSelect = vi.fn();
  render(
    <ProbeList
      requests={REQUESTS}
      selectedOp={null}
      shadowHashes={new Set()}
      onSelect={onSelect}
      {...overrides}
    />,
  );
  return { onSelect };
}

describe("ProbeList", () => {
  it("shows All lane count equal to total requests", () => {
    setup();
    const allChip = screen.getByRole("button", { name: /^all\s+\d/i });
    expect(allChip.textContent).toContain("5");
  });

  it("shows Probeable lane count excluding non-probeable ops", () => {
    setup();
    const chip = screen.getByRole("button", { name: /^probeable\s+\d/i });
    expect(chip.textContent).toContain("4");
  });

  it("shows Relative lane count for ops without absolute URL", () => {
    setup();
    const chip = screen.getByRole("button", { name: /^relative\s+\d/i });
    expect(chip.textContent).toContain("2"); // ws-feed + relative
  });

  it("shows WS lane count", () => {
    setup();
    const chip = screen.getByRole("button", { name: /^ws\s+\d/i });
    expect(chip.textContent).toContain("1");
  });

  it("filters rows when Probeable lane is active", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: /^probeable/i }));
    expect(screen.queryByText("/api/internal")).toBeNull();
    expect(screen.getByText("/api/checkout/session")).toBeTruthy();
  });

  it("filters rows when WS lane is active", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: /^ws\s/i }));
    expect(screen.getByText("/ws/notifications")).toBeTruthy();
    expect(screen.queryByText("/api/checkout/session")).toBeNull();
  });

  it("search filters by path substring", async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByRole("searchbox"), "checkout");
    expect(screen.getByText("/api/checkout/session")).toBeTruthy();
    expect(screen.queryByText("/api/items")).toBeNull();
  });

  it("shows 'No requests match' when nothing matches search", async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByRole("searchbox"), "zzz_no_match");
    expect(screen.getByText(/no requests match/i)).toBeTruthy();
  });

  it("groups rows by first path segment after /api", () => {
    setup();
    expect(screen.getByText("checkout")).toBeTruthy(); // group header
    expect(screen.getByText("items")).toBeTruthy();
  });

  it("falls back to host for non-/api paths", () => {
    setup();
    // WS request has path /ws/notifications — no /api/ match, so groups by host
    expect(screen.getByText("api.example.com")).toBeTruthy();
  });

  it("calls onSelect when a row is clicked", async () => {
    const user = userEvent.setup();
    const { onSelect } = setup();
    await user.click(screen.getByText("/api/items"));
    expect(onSelect).toHaveBeenCalledWith("items");
  });

  it("marks selected row with aria-current", () => {
    setup({ selectedOp: "items" });
    const rows = screen.getAllByRole("button", { name: /\/api\/items/i });
    const selectedRow = rows.find((el) => el.getAttribute("aria-current") === "true");
    expect(selectedRow).toBeTruthy();
  });

  it("shows shadow marker for hashes in shadowHashes set", () => {
    setup({ shadowHashes: new Set(["checkout_hash"]) });
    const shadowMarkers = screen.getAllByText("shadow");
    expect(shadowMarkers.length).toBeGreaterThanOrEqual(1);
  });
});
