import { describe, it, expect } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import { HostsPage } from "./HostsPage";
import type { HostsResponse } from "../../api/types";

const data: HostsResponse = {
  run_id: "r1",
  count: 3,
  in_scope: 2,
  endpoints_unattributed: 2,
  inferred_unattributed: 1,
  suspected_unattributed: 1,
  hosts: [
    { host: "acme.io", in_scope: true, declared: false, assets: 3, endpoints: 0, inferred: 4, suspected: 1, routes: 1, techs: 1 },
    { host: "api.acme.io", in_scope: true, declared: false, assets: 0, endpoints: 5, inferred: 0, suspected: 3, routes: 6, techs: 0 },
    { host: "cdn.evil.com", in_scope: false, declared: false, assets: 1, endpoints: 2, inferred: 0, suspected: 0, routes: 3, techs: 0 },
  ],
};

describe("HostsPage", () => {
  it("lists hosts with a scope badge and per-host counts", () => {
    render(<HostsPage data={data} />);
    expect(screen.getByText("api.acme.io")).toBeInTheDocument();
    const row = screen.getByText("cdn.evil.com").closest("tr") as HTMLElement;
    expect(within(row).getByText("out of scope")).toBeInTheDocument();
    expect(within(row).getByText("2")).toBeInTheDocument(); // its confirmed API count
  });

  it("surfaces host-less endpoints honestly in the summary", () => {
    render(<HostsPage data={data} />);
    expect(screen.getByText(/no resolved host/i)).toBeInTheDocument();
  });

  it("filters by scope", () => {
    render(<HostsPage data={data} />);
    fireEvent.click(screen.getByRole("button", { name: "Out of scope" }));
    expect(screen.queryByText("acme.io")).not.toBeInTheDocument();
    expect(screen.queryByText("api.acme.io")).not.toBeInTheDocument();
    expect(screen.getByText("cdn.evil.com")).toBeInTheDocument();
  });

  it("filters by name substring", () => {
    render(<HostsPage data={data} />);
    fireEvent.change(screen.getByLabelText(/filter hosts by name/i), {
      target: { value: "evil" },
    });
    expect(screen.getByText("cdn.evil.com")).toBeInTheDocument();
    expect(screen.queryByText("api.acme.io")).not.toBeInTheDocument();
  });

  it("sorts by a count column when its header is clicked", () => {
    render(<HostsPage data={data} />);
    // Default order is host-asc: acme.io, api.acme.io, cdn.evil.com.
    fireEvent.click(screen.getByRole("button", { name: /^API/ }));
    const names = screen.getAllByText(/\./).filter((el) => el.className === "hosts-host-name");
    // endpoints desc: api.acme.io (5), cdn.evil.com (2), acme.io (0)
    expect(names.map((n) => n.textContent)).toEqual(["api.acme.io", "cdn.evil.com", "acme.io"]);
  });

  it("shows an empty state when nothing was discovered", () => {
    render(
      <HostsPage
        data={{ run_id: "r1", count: 0, in_scope: 0, endpoints_unattributed: 0, inferred_unattributed: 0, suspected_unattributed: 0, hosts: [] }}
      />,
    );
    expect(screen.getByText(/no hosts discovered/i)).toBeInTheDocument();
  });

  it("renders a Suspected calls column and sorts by it", () => {
    render(<HostsPage data={data} />);
    fireEvent.click(screen.getByRole("button", { name: /Suspected calls/ }));
    const names = screen.getAllByText(/\./).filter((el) => el.className === "hosts-host-name");
    // suspected desc: api.acme.io (3), acme.io (1), cdn.evil.com (0)
    expect(names.map((n) => n.textContent)).toEqual(["api.acme.io", "acme.io", "cdn.evil.com"]);
  });

  it("renders a Page routes column (page_route hosts) and sorts by it", () => {
    render(<HostsPage data={data} />);
    fireEvent.click(screen.getByRole("button", { name: /Page routes/ }));
    const names = screen.getAllByText(/\./).filter((el) => el.className === "hosts-host-name");
    // routes desc: api.acme.io (6), cdn.evil.com (3), acme.io (1)
    expect(names.map((n) => n.textContent)).toEqual(["api.acme.io", "cdn.evil.com", "acme.io"]);
  });

  it("gives inferred APIs their own column, never blended into Suspected calls (QA)", () => {
    render(<HostsPage data={data} />);
    // Column headers use the Findings Type-facet labels, one lane per column.
    for (const name of [/^API/, /^Inferred API/, /^Suspected calls/, /^Page routes/]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("button", { name: /^Inferred API/ }));
    const names = screen.getAllByText(/\./).filter((el) => el.className === "hosts-host-name");
    // inferred desc: acme.io (4) first — its 1 suspected call is counted separately.
    expect(names[0].textContent).toBe("acme.io");
  });

  it("reports host-less findings per lane, in the lanes' own labels", () => {
    render(<HostsPage data={data} />);
    expect(
      screen.getByText("no resolved host: 2 API · 1 inferred API · 1 suspected call"),
    ).toBeInTheDocument();
  });
});
