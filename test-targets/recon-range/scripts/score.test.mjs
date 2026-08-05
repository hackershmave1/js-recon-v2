import { test } from "node:test";
import assert from "node:assert/strict";
import { scoreFindings } from "./score.mjs";

const key = {
  should_find: [
    { id: "ep-profile", method: "GET", operation: "/api/v1/profile", params: [] },
    { id: "ep-order", method: "POST", operation: "/api/v1/orders", params: [{ location: "body", name: "sku" }] },
    { id: "ep-ga", method: "GET", host: "www.google-analytics.com", params: [] },
  ],
  known_blind_spots: [{ id: "bs-wrapper", probe: "/api/v1/hidden" }],
  secrets: { must: { stripe: 2, aws: 1 }, info: ["github"] },
  coverage_asserts: { min_unattributed: 2, source_map_ok: ["capture"], require_sources_recovered: true },
};
const good = {
  coverage: { source_map: "capture", sources_recovered: 3, unattributed: 2 },
  findings: [
    { type: "endpoint", value: "GET /api/v1/profile", attributes: { kind: "fetch", method: "GET" }, occurrences: [{ source_path: "src/api/profile.js" }] },
    { type: "endpoint", value: "POST /api/v1/orders", occurrences: [{ source_path: "src/api/profile.js" }] },
    { type: "param", value: "POST /api/v1/orders body:sku", attributes: { location: "body", name: "sku" }, occurrences: [] },
    { type: "endpoint", value: "GET /g/collect?tid&v", occurrences: [{ host: "www.google-analytics.com" }] },
    { type: "secret", value: "stripe:aaa", occurrences: [{ source_path: "input.js" }] },
    { type: "secret", value: "stripe:bbb", occurrences: [{ source_path: "input.js" }] },
    { type: "secret", value: "aws:ccc", occurrences: [{ source_path: "input.js" }] },
  ],
};

test("clean payload PASSes", () => {
  const r = scoreFindings(good, key);
  assert.equal(r.pass, true, JSON.stringify(r));
});
test("missing endpoint FAILs", () => {
  const p = { ...good, findings: good.findings.filter(f => f.value !== "GET /api/v1/profile") };
  const r = scoreFindings(p, key);
  assert.equal(r.pass, false);
  assert.deepEqual(r.missedEndpoints, ["ep-profile"]);
});
test("missing body param FAILs", () => {
  const p = { ...good, findings: good.findings.filter(f => f.type !== "param") };
  assert.equal(scoreFindings(p, key).pass, false);
});
test("only one stripe secret FAILs the count gate", () => {
  const p = { ...good, findings: good.findings.filter(f => f.value !== "stripe:bbb") };
  assert.deepEqual(scoreFindings(p, key).secretMisses, ["stripe>=2 (got 1)"]);
});
test("no recovered source_path FAILs the source-map gate", () => {
  const p = { ...good, findings: good.findings.map(f => f.type === "endpoint" ? { ...f, occurrences: [{ source_path: "input.js" }] } : f) };
  assert.ok(scoreFindings(p, key).covFail.some(x => x.includes("recovered")));
});
test("a resolved blind-spot probe is flagged (not a hard fail)", () => {
  const p = { ...good, findings: [...good.findings, { type: "endpoint", value: "GET /api/v1/hidden", occurrences: [] }] };
  const r = scoreFindings(p, key);
  assert.deepEqual(r.blindViolations, ["bs-wrapper"]);
  assert.equal(r.pass, true);
});
