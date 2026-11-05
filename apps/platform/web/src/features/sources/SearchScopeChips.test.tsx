import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SearchScopeChips, scopeFor, folderOf } from "./SearchScopeChips";
import type { SourceFile } from "../../api/types";

const original: SourceFile = { path: "node_modules/@statsig/client-core/src/a.js", kind: "source", fetch_status: "ok", asset_url: "https://acme.io/app.js" };
const rootFile: SourceFile = { path: "input.js", kind: "upload", fetch_status: "ok", asset_url: null };

describe("SearchScopeChips", () => {
  it("folder is the open file's tree folder (scheme stripped, like the tree)", () => {
    expect(folderOf("https://acme.io/static/app.js")).toBe("acme.io/static");
    expect(folderOf(original.path)).toBe("node_modules/@statsig/client-core/src");
    expect(folderOf("input.js")).toBeNull(); // a root-level file has no folder
  });

  it("maps the chosen chip to the request scope", () => {
    expect(scopeFor("all", original)).toEqual({});
    expect(scopeFor("folder", original)).toEqual({ folder: "node_modules/@statsig/client-core/src" });
    expect(scopeFor("file", original)).toEqual({ path: original.path, assetUrl: "https://acme.io/app.js" });
  });

  it("offers All, This folder (named) and This file; picking one reports it", async () => {
    const onChange = vi.fn();
    render(<SearchScopeChips value="all" file={original} onChange={onChange} />);
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: /This folder/ }));
    expect(onChange).toHaveBeenCalledWith("folder");
    expect(screen.getByRole("button", { name: /This folder/ })).toHaveAttribute("title", "node_modules/@statsig/client-core/src");
  });

  it("disables This folder for a root-level file and both for no file", () => {
    const { rerender } = render(<SearchScopeChips value="all" file={rootFile} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: /This folder/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "This file" })).toBeEnabled();
    rerender(<SearchScopeChips value="all" file={null} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "This file" })).toBeDisabled();
  });
});
