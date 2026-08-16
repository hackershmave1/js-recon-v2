import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { TechPage } from "./TechPage";
import type { TechnologiesResponse } from "../../api/types";

const data: TechnologiesResponse = {
  run_id: "r1", count: 2,
  hosts: {
    "acme.io": [
      { name: "Nginx", categories: ["Web servers"], version: "1.25.3", confidence: 100, evidence: ["server: nginx/1.25.3"] },
      { name: "jQuery", categories: ["JavaScript libraries"], version: "3.5.1", confidence: 100, evidence: ["scriptSrc: jquery-3.5.1.min.js"] },
    ],
  },
};

describe("TechPage", () => {
  it("renders per-host technologies with version, category and confidence", () => {
    render(<TechPage data={data} />);
    expect(screen.getByText("acme.io")).toBeInTheDocument();
    const nginx = screen.getByText("Nginx").closest("tr") as HTMLElement;
    expect(within(nginx).getByText("1.25.3")).toBeInTheDocument();
    expect(within(nginx).getByText("Web servers")).toBeInTheDocument();
    expect(within(nginx).getByText("100")).toBeInTheDocument();
    expect(screen.getByText("jQuery")).toBeInTheDocument();
  });

  it("shows an empty state when nothing was detected", () => {
    render(<TechPage data={{ run_id: "r1", count: 0, hosts: {} }} />);
    expect(screen.getByText(/no technologies/i)).toBeInTheDocument();
  });
});
