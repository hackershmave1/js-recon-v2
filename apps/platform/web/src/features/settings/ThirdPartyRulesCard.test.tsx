import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThirdPartyRulesCard } from "./ThirdPartyRulesCard";
import * as api from "./settingsApi";
import type { ThirdPartyRules } from "./settingsApi";

const RULES: ThirdPartyRules = {
  builtin: { hosts: { analytics: ["google-analytics.com", "hotjar.com"], telemetry: ["sentry.io"] }, file_patterns: ["*/jquery*.min.js"] },
  team: {
    hosts: [{ id: "h1", kind: "host", value: "tracker.example.com", created_at: "2026-11-16T00:00:00Z", created_by: "ana@acme.io" }],
    file_patterns: [{ id: "p1", kind: "file_pattern", value: "/vendor/chat*.js", created_at: "2026-11-16T00:00:00Z", created_by: null }],
  },
  limits: { hosts: 200, file_patterns: 100, pattern_length: 200 },
  can_edit: true,
};

beforeEach(() => vi.restoreAllMocks());

describe("ThirdPartyRulesCard", () => {
  it("lists the built-in rules read-only and the team's additions with a remove button", async () => {
    vi.spyOn(api, "getThirdPartyRules").mockResolvedValue(RULES);
    render(<ThirdPartyRulesCard tenantId="t1" />);
    expect(await screen.findByText("tracker.example.com")).toBeInTheDocument();
    expect(screen.getByText("/vendor/chat*.js")).toBeInTheDocument();
    const builtin = screen.getByRole("region", { name: "Built-in rules" });
    expect(within(builtin).getByText(/google-analytics\.com/)).toBeInTheDocument();
    expect(within(builtin).getByText("*/jquery*.min.js")).toBeInTheDocument();
    expect(within(builtin).queryByRole("button")).toBeNull(); // built-in is read-only
    expect(screen.getAllByRole("button", { name: /^Remove / })).toHaveLength(2);
  });

  it("adds a host and reloads", async () => {
    const get = vi.spyOn(api, "getThirdPartyRules").mockResolvedValueOnce(RULES).mockResolvedValue(RULES);
    const add = vi.spyOn(api, "addThirdPartyRule").mockResolvedValue({ ...RULES.team.hosts[0], value: "x.example.com" });
    render(<ThirdPartyRulesCard tenantId="t1" />);
    await userEvent.type(await screen.findByLabelText("Host"), "x.example.com");
    await userEvent.click(screen.getByRole("button", { name: "Add host" }));
    expect(add).toHaveBeenCalledWith("t1", "host", "x.example.com");
    expect(await screen.findByRole("status")).toHaveTextContent("Added");
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("adds a file pattern", async () => {
    vi.spyOn(api, "getThirdPartyRules").mockResolvedValue(RULES);
    const add = vi.spyOn(api, "addThirdPartyRule").mockResolvedValue(RULES.team.file_patterns[0]);
    render(<ThirdPartyRulesCard tenantId="t1" />);
    await userEvent.type(await screen.findByLabelText("File pattern"), "/plugins/foo*.js");
    await userEvent.click(screen.getByRole("button", { name: "Add pattern" }));
    expect(add).toHaveBeenCalledWith("t1", "file_pattern", "/plugins/foo*.js");
  });

  it("shows the server's reason when a rule is refused", async () => {
    vi.spyOn(api, "getThirdPartyRules").mockResolvedValue(RULES);
    vi.spyOn(api, "addThirdPartyRule").mockRejectedValue(new Error("pattern needs at least 4 characters"));
    render(<ThirdPartyRulesCard tenantId="t1" />);
    await userEvent.type(await screen.findByLabelText("File pattern"), "*");
    await userEvent.click(screen.getByRole("button", { name: "Add pattern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("pattern needs at least 4 characters");
  });

  it("removes a team rule", async () => {
    vi.spyOn(api, "getThirdPartyRules").mockResolvedValue(RULES);
    const remove = vi.spyOn(api, "removeThirdPartyRule").mockResolvedValue();
    render(<ThirdPartyRulesCard tenantId="t1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Remove tracker.example.com" }));
    expect(remove).toHaveBeenCalledWith("t1", "h1");
  });

  it("without a login the lists show but nothing can be changed", async () => {
    vi.spyOn(api, "getThirdPartyRules").mockResolvedValue({ ...RULES, can_edit: false });
    render(<ThirdPartyRulesCard tenantId="t1" />);
    await screen.findByText("tracker.example.com");
    expect(screen.queryByRole("button", { name: /Remove|Add/ })).toBeNull();
  });

  it("says so when the rules cannot be loaded", async () => {
    vi.spyOn(api, "getThirdPartyRules").mockRejectedValue(new Error("boom"));
    render(<ThirdPartyRulesCard tenantId="t1" />);
    expect(await screen.findByText(/Could not load/)).toBeInTheDocument();
  });
});
