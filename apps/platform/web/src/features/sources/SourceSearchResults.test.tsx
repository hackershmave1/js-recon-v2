import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SourceSearchResults } from "./SourceSearchResults";
import type { SourceSearchResponse } from "../../api/types";

const hit = (over: Partial<SourceSearchResponse["matches"][number]> = {}) => ({
  path: "https://acme.io/app.js", kind: "asset", asset_url: null, line: 12, col: 4,
  snippet: "fetch('/api/token')", exact: true, ...over,
});
const resp = (over: Partial<SourceSearchResponse> = {}): SourceSearchResponse => ({
  run_id: "r", count: 1, total: 1, files_scanned: 211, files_total: 211, unindexed: 0,
  partial: false, matches: [hit()], ...over,
});

describe("SourceSearchResults", () => {
  it("summarises honestly: matches shown of total, files searched of total", () => {
    render(<SourceSearchResults response={resp({ count: 2000, total: 4120 })} searching={false} error={null} onJump={vi.fn()} />);
    expect(screen.getByText("Showing 2000 of 4120 matches · searched 211 of 211 files")).toBeInTheDocument();
  });

  it("says when the search stopped early", () => {
    render(<SourceSearchResults response={resp({ partial: true, files_scanned: 150 })} searching={false} error={null} onJump={vi.fn()} />);
    expect(screen.getByText(/stopped early/i)).toBeInTheDocument();
    expect(screen.getByText(/searched 150 of 211 files/)).toBeInTheDocument();
  });

  it("marks hits from not-yet-indexed files as having no line number", async () => {
    const onJump = vi.fn();
    const m = hit({ line: null, exact: false });
    render(<SourceSearchResults response={resp({ unindexed: 3, matches: [m] })} searching={false} error={null} onJump={onJump} />);
    expect(screen.getByText(/3 files analyzed before search indexing/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /app\.js/ }));
    expect(onJump).toHaveBeenCalledWith(m);
    expect(screen.getByText("app.js")).toBeInTheDocument(); // no ":line"
  });

  it("says older files are being indexed in the background, with progress", () => {
    render(<SourceSearchResults response={resp({ unindexed: 91, matches: [hit({ line: null, exact: false })] })}
      searching={false} error={null} indexStatus={{ indexed: 120, total: 211, indexing: true }} onJump={vi.fn()} />);
    expect(screen.getByText(/Indexing older files in the background \(120 of 211 done\)/)).toBeInTheDocument();
  });

  it("shows an error instead of 'No matches' when the search failed", () => {
    render(<SourceSearchResults response={null} searching={false} error="Search failed — try again" onJump={vi.fn()} />);
    expect(screen.getByText("Search failed — try again")).toBeInTheDocument();
    expect(screen.queryByText("No matches.")).toBeNull();
  });
});
