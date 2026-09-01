import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GraphQLPage } from "./GraphQLPage";
import type { FindingsResponse, Finding, Occurrence } from "../../api/types";

const occ = (over: Partial<Occurrence> = {}): Occurrence => ({
  host: null, raw_url: null, source_path: null, line: null, col: null,
  offset_start: null, offset_end: null, evidence: null, engine: null,
  confidence: null, verified: null, asset_url: null, ...over,
});
const finding = (over: Partial<Finding> = {}): Finding => ({
  finding_hash: "h", type: "graphql", value: null, path: null, severity: null,
  attributes: {}, first_stage: "analyze", revealable: false, triage: null,
  spec_status: null, occurrences: [], ...over,
});
const resp = (findings: Finding[]): FindingsResponse => ({
  run_id: "r1", count: findings.length, coverage: null, spec: null, findings,
});

describe("GraphQLPage", () => {
  it("groups operations by kind in query → mutation → subscription → fragment order", () => {
    // Supplied out of order on purpose: the page must impose the canonical order.
    const data = resp([
      finding({ finding_hash: "f1", value: "fragment UserFields on User",
        attributes: { kind: "fragment", name: "UserFields", fields: ["id"], on_type: "User" } }),
      finding({ finding_hash: "s1", value: "subscription OnPing",
        attributes: { kind: "subscription", name: "OnPing", fields: ["ping"] } }),
      finding({ finding_hash: "m1", value: "mutation CreateOrg",
        attributes: { kind: "mutation", name: "CreateOrg", fields: ["org"] } }),
      finding({ finding_hash: "q1", value: "query Me",
        attributes: { kind: "query", name: "Me", fields: ["id", "email"] } }),
    ]);
    render(<GraphQLPage data={data} onJumpToSource={vi.fn()} />);

    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(["Queries1", "Mutations1", "Subscriptions1", "Fragments1"]);
    expect(screen.getByText("4 total")).toBeInTheDocument();
  });

  it("renders a name, its fields, and the fragment on-type", () => {
    const data = resp([
      finding({ finding_hash: "q1", value: "query Me",
        attributes: { kind: "query", name: "Me", fields: ["id", "email"] } }),
      finding({ finding_hash: "f1", value: "fragment UserFields on User",
        attributes: { kind: "fragment", name: "UserFields", fields: ["avatar"], on_type: "User" } }),
    ]);
    render(<GraphQLPage data={data} onJumpToSource={vi.fn()} />);

    expect(screen.getByText("Me")).toBeInTheDocument();
    expect(screen.getByText("id, email")).toBeInTheDocument();
    expect(screen.getByText("UserFields")).toBeInTheDocument();
    expect(screen.getByText("on User")).toBeInTheDocument();
  });

  it("counts each group and shows the running total", () => {
    const data = resp([
      finding({ finding_hash: "q1", attributes: { kind: "query", name: "A", fields: [] } }),
      finding({ finding_hash: "q2", attributes: { kind: "query", name: "B", fields: [] } }),
      finding({ finding_hash: "m1", attributes: { kind: "mutation", name: "C", fields: [] } }),
    ]);
    render(<GraphQLPage data={data} onJumpToSource={vi.fn()} />);

    const queries = screen.getByRole("heading", { name: /queries/i });
    expect(within(queries).getByText("2")).toBeInTheDocument();
    expect(screen.getByText("3 total")).toBeInTheDocument();
  });

  it("falls back to the raw value when a document has no name (anonymous op)", () => {
    const data = resp([
      finding({ finding_hash: "q1", value: "query · a1b2c3",
        attributes: { kind: "query", name: null, fields: ["id"] } }),
    ]);
    render(<GraphQLPage data={data} onJumpToSource={vi.fn()} />);
    expect(screen.getByText("query · a1b2c3")).toBeInTheDocument();
  });

  it("links the source location into Sources like the Findings page does", async () => {
    const onJump = vi.fn();
    const data = resp([
      finding({ finding_hash: "q1", value: "query Me",
        attributes: { kind: "query", name: "Me", fields: ["id"] },
        occurrences: [occ({ asset_url: "https://acme.io/app.js", source_path: "app.js", line: 42 })] }),
    ]);
    render(<GraphQLPage data={data} onJumpToSource={onJump} />);

    const jump = screen.getByRole("button", { name: /open https:\/\/acme\.io\/app\.js in sources/i });
    expect(jump).toHaveTextContent("https://acme.io/app.js:42");
    await userEvent.click(jump);
    expect(onJump).toHaveBeenCalledWith({ sourcePath: "app.js", assetUrl: "https://acme.io/app.js", line: 42 });
  });

  it("renders a clean empty state (never fake rows) when no graphql findings exist", () => {
    // A non-graphql finding must not leak into the GraphQL surface.
    const data = resp([finding({ finding_hash: "e1", type: "endpoint", value: "/api/x" })]);
    render(<GraphQLPage data={data} onJumpToSource={vi.fn()} />);
    expect(screen.getByText("No GraphQL operations recovered.")).toBeInTheDocument();
    expect(screen.queryByText("/api/x")).toBeNull();
  });

  it("always shows the static-detection honesty note (counts are a floor)", () => {
    render(<GraphQLPage data={resp([])} onJumpToSource={vi.fn()} />);
    expect(screen.getByText(/a floor, not a ceiling/i)).toBeInTheDocument();
  });
});
