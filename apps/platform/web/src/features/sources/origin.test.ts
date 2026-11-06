import { describe, it, expect } from "vitest";
import { isThirdParty, packageOf, treeSegments, THIRD_PARTY_ROOT } from "./origin";

// Shared with apps/platform/src/recon/findings/origin_test.py — keep both in step.
const PNPM_SCOPED = "node_modules/.pnpm/@statsig+client-core@3.31.2/node_modules/@statsig/client-core/src/EventLogger.js";
const PNPM_PEER = "node_modules/.pnpm/@module-federation+runtime-core@2.6.0_node-fetch@2.7.0/node_modules/@module-federation/runtime-core/dist/plugins/snapshot/SnapshotHandler.cjs";
const THIRD: [string, string, string | null][] = [
  [PNPM_SCOPED, "@statsig/client-core", "3.31.2"],
  [PNPM_PEER, "@module-federation/runtime-core", "2.6.0"],
  ["node_modules/.pnpm/lodash-es@4.17.21/node_modules/lodash-es/_Set.js", "lodash-es", "4.17.21"],
  ["webpack/acme/node_modules/axios/lib/core/Axios.js", "axios", null],
  ["node_modules/@babel/runtime/helpers/esm/defineProperty.js", "@babel/runtime", null],
  [".yarn/cache/react-npm-18.2.0-abc.zip/node_modules/react/index.js", "react", null],
  ["bower_components/jquery/dist/jquery.js", "jquery", null],
  ["node_modules/foo/node_modules/@bar/baz/x.js", "@bar/baz", null], // nested install
];
const APP = [
  "packages/core-web/dist/logger/logger.service.js", "src/api/users.ts",
  "webpack/acme/./src/node_modules_helper.js", "https://acme.io/static/app.js", "input.js", "",
];

describe("origin (mirror of recon.findings.origin)", () => {
  it.each(THIRD)("%s is third-party in package %s", (path, name, version) => {
    expect(isThirdParty(path)).toBe(true);
    expect(packageOf(path)).toEqual({ name, version });
  });
  it.each(APP)("%s is app code or unclassified", (path) => {
    expect(isThirdParty(path)).toBe(false);
    expect(packageOf(path)).toBeNull();
  });
  it("treat-as-app-code patterns", () => {
    const p = "node_modules/.pnpm/@acme+api-client@1.0.0/node_modules/@acme/api-client/index.js";
    expect(isThirdParty(p, ["@acme/*"])).toBe(false);
    expect(isThirdParty(p, ["@acme/api-client"])).toBe(false);
    expect(isThirdParty(p, ["@acme/other", "acme"])).toBe(true);
  });
  it("groups a third-party file under its package in the tree", () => {
    expect(treeSegments(PNPM_SCOPED, [], [])).toEqual({
      segs: [THIRD_PARTY_ROOT, "@statsig/client-core 3.31.2", "src", "EventLogger.js"],
      packageName: "@statsig/client-core",
    });
    expect(treeSegments("src/a.js", ["src", "a.js"], []).segs).toEqual(["src", "a.js"]);
    // the package name recurring inside the package must not collapse two files onto one node
    expect(treeSegments("node_modules/foo/index.js", [], []).segs).toEqual([THIRD_PARTY_ROOT, "foo", "index.js"]);
    expect(treeSegments("node_modules/foo/lib/foo/index.js", [], []).segs)
      .toEqual([THIRD_PARTY_ROOT, "foo", "lib", "foo", "index.js"]);
  });
});
