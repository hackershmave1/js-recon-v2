import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { CodeViewer } from "./CodeViewer";

// The viewer highlights the visible window via loadPrism/highlightLine; mock them out so
// these tests assert on plain text (highlight.ts has its own tests).
vi.mock("./highlight", () => ({
  loadPrism: vi.fn(() => Promise.reject(new Error("no highlight in test"))),
  highlightLine: vi.fn(() => []),
}));

describe("CodeViewer", () => {
  it("windows a large multi-line file — only the viewport is committed to the DOM", () => {
    const text = Array.from({ length: 5000 }, (_, i) => `line ${i}`).join("\n");
    render(<CodeViewer text={text} truncated={false} marks={null} />);
    const rows = document.querySelectorAll(".sv-line");
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThan(300); // windowed to the viewport, not 5000
  });

  it("caps the rendered width of a pathological single long line", () => {
    const text = "a".repeat(250_000); // one line, well over RENDER_MAX_LINE (100k)
    render(<CodeViewer text={text} truncated={false} marks={null} />);
    const row = document.querySelector(".sv-code-txt");
    // 100k chars + the " …" truncation marker; never the full 250k (which would jank layout).
    expect((row?.textContent ?? "").length).toBeLessThanOrEqual(100_002);
  });

  it("applies the focus class to the jumped-to line", () => {
    const text = Array.from({ length: 30 }, (_, i) => `line ${i}`).join("\n");
    render(<CodeViewer text={text} truncated={false} marks={null} focusLine={3} />);
    const focused = document.querySelector(".sv-line.focus");
    expect(focused).not.toBeNull();
    expect(focused?.querySelector(".sv-ln")?.textContent).toBe("3");
  });

  it("marks a finding line and shows its type", () => {
    const text = "a\nb\nc";
    render(<CodeViewer text={text} truncated={false} marks={new Map([[2, "secret"]])} />);
    const marked = document.querySelector(".sv-line.marked");
    expect(marked).not.toBeNull();
    expect(marked?.textContent).toContain("secret");
  });

  it("flags a truncated file", () => {
    render(<CodeViewer text="x" truncated={true} marks={null} />);
    expect(document.querySelector(".sv-note.sv-warn")?.textContent).toMatch(/truncated/i);
  });
});

describe("CodeViewer exact ranges", () => {
  it("highlights exactly the finding's token, and the jumped-to one as the focus", () => {
    const text = 'const a = 1;\nfetch("/api/users", { method: "POST" });';
    const loc = { line: 2, col: 6, end_line: 2, end_col: 18 };
    render(<CodeViewer text={text} truncated={false} marks={null} ranges={[loc]} focusRange={loc} focusLine={2} />);
    const hits = document.querySelectorAll("mark.sv-hit");
    expect([...hits].map((h) => h.textContent)).toEqual(['"/api/users"']);
    expect(hits[0].classList.contains("focus")).toBe(true);
  });
});
