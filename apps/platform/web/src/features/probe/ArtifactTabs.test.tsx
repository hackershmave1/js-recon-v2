import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ArtifactTabs } from "./ArtifactTabs";

describe("ArtifactTabs", () => {
  it("shows 'Not probeable' message when artifacts is null", () => {
    render(<ArtifactTabs artifacts={null} />);
    expect(screen.getByText(/not probeable/i)).toBeTruthy();
  });

  it("shows only present artifact tabs", () => {
    render(<ArtifactTabs artifacts={{ curl: "curl example.com" }} />);
    expect(screen.getByRole("tab", { name: "curl" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "HTTP" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "websocat" })).toBeNull();
  });

  it("shows Python tab only when http artifact is present", () => {
    render(<ArtifactTabs artifacts={{ http: "GET / HTTP/1.1\nHost: api.example.com" }} />);
    expect(screen.getByRole("tab", { name: "HTTP" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /python/i })).toBeTruthy();
  });

  it("omits Python tab when http is absent", () => {
    render(<ArtifactTabs artifacts={{ curl: "curl example.com" }} />);
    expect(screen.queryByRole("tab", { name: /python/i })).toBeNull();
  });

  it("shows all three tabs + Python for a full artifact set", () => {
    render(<ArtifactTabs artifacts={{ curl: "c", http: "h", websocat: "w" }} />);
    expect(screen.getByRole("tab", { name: "curl" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "HTTP" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "websocat" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /python/i })).toBeTruthy();
  });

  it("switches tab content when a tab is clicked", async () => {
    const user = userEvent.setup();
    render(<ArtifactTabs artifacts={{ curl: "curl_content", http: "http_content" }} />);
    expect(screen.getByText("curl_content")).toBeTruthy();
    await user.click(screen.getByRole("tab", { name: "HTTP" }));
    expect(screen.getByText("http_content")).toBeTruthy();
  });

  it("copy button writes active tab content to clipboard", async () => {
    const user = userEvent.setup();
    const write = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(navigator.clipboard, "writeText").mockImplementation(write);
    render(<ArtifactTabs artifacts={{ curl: "curl_cmd" }} />);
    await user.click(screen.getByRole("button", { name: /copy/i }));
    expect(write).toHaveBeenCalledWith("curl_cmd");
  });

  it("shows 'Copied ✓' feedback after copy", async () => {
    const user = userEvent.setup();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    render(<ArtifactTabs artifacts={{ curl: "x" }} />);
    await user.click(screen.getByRole("button", { name: /copy/i }));
    expect(screen.getByText(/copied/i)).toBeTruthy();
    vi.useRealTimers();
  });

  it("WS op shows only websocat tab", () => {
    render(<ArtifactTabs artifacts={{ websocat: "websocat ws://example.com" }} />);
    expect(screen.getByRole("tab", { name: "websocat" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "curl" })).toBeNull();
  });

  beforeEach(() => {
    vi.restoreAllMocks();
  });
});
