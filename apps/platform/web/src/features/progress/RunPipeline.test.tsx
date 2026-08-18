import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RunPipeline } from "./RunPipeline";

describe("RunPipeline", () => {
  it("marks prior stages complete and the current stage active", () => {
    render(<RunPipeline state="analyzing" stage="analyzing" pct={68} done={2} total={3} etaSeconds={8} />);
    expect(screen.getByText("Discover").closest("li")!.className).toContain("is-complete");
    expect(screen.getByText("Fetch").closest("li")!.className).toContain("is-complete");
    const analyze = screen.getByText("Analyze").closest("li")!;
    expect(analyze.className).toContain("is-active");
    expect(analyze).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("Correlate").closest("li")!.className).toContain("is-pending");
  });

  it("shows a determinate bar with pct, count, and eta while active", () => {
    render(<RunPipeline state="fetching" stage="fetching" pct={40} done={2} total={5} etaSeconds={95} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "40");
    expect(screen.getByText("40%")).toBeInTheDocument();
    expect(screen.getByText("2 of 5")).toBeInTheDocument();
    expect(screen.getByText(/~1m 35s left/)).toBeInTheDocument();
  });

  it("marks every stage complete and hides the bar when the run is done", () => {
    render(<RunPipeline state="done" stage={null} pct={100} done={0} total={0} etaSeconds={null} />);
    for (const label of ["Discover", "Fetch", "Ingest", "Analyze", "Correlate"]) {
      expect(screen.getByText(label).closest("li")!.className).toContain("is-complete");
    }
    expect(screen.getByText(/5 of 5/)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("does not read as full success when a done crawl discovered nothing", () => {
    render(
      <RunPipeline state="done" stage={null} pct={100} done={0} total={0} etaSeconds={null} emptyCrawl />,
    );
    expect(screen.getByText(/no in-scope javascript found/i)).toBeInTheDocument();
    expect(screen.queryByText(/5 of 5/)).not.toBeInTheDocument();
  });

  it("marks the stopped stage and counts completed stages for a partial run", () => {
    render(<RunPipeline state="partial" stage="ingesting" pct={null} done={0} total={0} etaSeconds={null} />);
    expect(screen.getByText("Discover").closest("li")!.className).toContain("is-complete");
    expect(screen.getByText("Fetch").closest("li")!.className).toContain("is-complete");
    const ingest = screen.getByText("Ingest", { selector: ".rp-step-label" }).closest("li")!;
    expect(ingest.className).toContain("is-stopped");
    expect(ingest.className).toContain("is-warn");
    expect(screen.getByText("Analyze").closest("li")!.className).toContain("is-pending");
    expect(screen.getByText(/Stopped in/)).toBeInTheDocument();
    expect(screen.getByText(/2 of 5 stages completed/)).toBeInTheDocument();
  });

  it("renders no bar and all-pending stages while queued", () => {
    render(<RunPipeline state="queued" stage={null} pct={null} done={0} total={0} etaSeconds={null} />);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText("Discover").closest("li")!.className).toContain("is-pending");
  });
});
