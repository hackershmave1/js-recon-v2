import { describe, it, expect } from "vitest";
import { highlightJsLines } from "./highlight";

describe("highlightJsLines", () => {
  it("yields one span array per source line (matches split('\\n'))", async () => {
    const code = "const a = 1\nconst b = 2\n";
    const lines = await highlightJsLines(code);
    expect(lines).toHaveLength(code.split("\n").length); // 3 (trailing newline)
  });

  it("keeps a multi-line token (template literal) intact across lines (S3)", async () => {
    // A template literal is ONE Prism token whose content spans lines; splitting
    // rendered HTML would tear tags. We split token content, so text round-trips.
    const code = "const t = `line1\nline2`\nconst x = 1";
    const lines = await highlightJsLines(code);
    expect(lines).toHaveLength(3);
    const rebuilt = lines.map((l) => l.map((s) => s.text).join("")).join("\n");
    expect(rebuilt).toBe(code);
  });

  it("tags keywords and strings with token classes", async () => {
    const [spans] = await highlightJsLines("const s = 'hi'");
    expect(spans.some((s) => s.className.includes("keyword"))).toBe(true);
    expect(spans.some((s) => s.className.includes("string"))).toBe(true);
  });
});
