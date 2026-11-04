import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import * as api from "../../api/apiClient";
import { LocalTargetsCard } from "./LocalTargetsCard";

beforeEach(() => vi.restoreAllMocks());

describe("LocalTargetsCard", () => {
  it("reports the switch off and how to turn it on", async () => {
    vi.spyOn(api, "getServerHealth").mockResolvedValue({ status: "ok", allowLocalTargets: false });
    render(<LocalTargetsCard />);
    expect(await screen.findByText("Disabled")).toBeInTheDocument();
    expect(screen.getByText(/captures of localhost still upload and analyze/)).toBeInTheDocument();
    expect(screen.getByText("RECON_ALLOW_LOCAL_EGRESS=true")).toBeInTheDocument();
  });

  it("reports the switch on", async () => {
    vi.spyOn(api, "getServerHealth").mockResolvedValue({ status: "ok", allowLocalTargets: true });
    render(<LocalTargetsCard />);
    expect(await screen.findByText("Enabled")).toBeInTheDocument();
  });

  it("shows a loading state first, then 'unavailable' when the server can't be reached", async () => {
    vi.spyOn(api, "getServerHealth").mockRejectedValue(new Error("down"));
    render(<LocalTargetsCard />);
    expect(screen.getByText("Checking…")).toBeInTheDocument();
    expect(await screen.findByText("Status unavailable.")).toBeInTheDocument();
  });
});
