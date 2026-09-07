import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ParamsTable } from "./ParamsTable";
import type { ReconstructedRequest } from "../../api/types";

function makeReq(overrides: Partial<ReconstructedRequest> = {}): ReconstructedRequest {
  return {
    operation: "op1", method: "GET", path: "/api/items", hosts: [],
    query_params: [], body_params: [], content_type: null,
    example_url: "https://api.example.com/api/items",
    probeable: true, endpoint_hashes: [], artifacts: null,
    ...overrides,
  };
}

describe("ParamsTable", () => {
  it("renders nothing when no params", () => {
    const { container } = render(<ParamsTable req={makeReq()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders query params with name and example", () => {
    const req = makeReq({ query_params: [{ name: "page", example: "1" }] });
    render(<ParamsTable req={req} />);
    expect(screen.getByText("page")).toBeTruthy();
    expect(screen.getByText("query")).toBeTruthy();
    expect(screen.getByText("1")).toBeTruthy();
  });

  it("renders null example as dash", () => {
    const req = makeReq({ query_params: [{ name: "q", example: null }] });
    render(<ParamsTable req={req} />);
    expect(screen.getByText("—")).toBeTruthy();
  });

  it("renders body params with in=body and no example", () => {
    const req = makeReq({ body_params: ["name", "email"] });
    render(<ParamsTable req={req} />);
    expect(screen.getByText("name")).toBeTruthy();
    expect(screen.getByText("email")).toBeTruthy();
    const bodyLabels = screen.getAllByText("body");
    expect(bodyLabels).toHaveLength(2);
  });

  it("row count equals query + body param count", () => {
    const req = makeReq({
      query_params: [{ name: "a", example: "x" }, { name: "b", example: null }],
      body_params: ["c"],
    });
    render(<ParamsTable req={req} />);
    const rows = screen.getAllByRole("row");
    // 1 header + 3 data rows
    expect(rows).toHaveLength(4);
  });
});
