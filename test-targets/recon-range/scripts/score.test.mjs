import { test } from "node:test";
import assert from "node:assert/strict";
import { findingsUrl, scoreFindings } from "./score.mjs";

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

test("the CLI scores the full captured set, and reads the default view separately", () => {
  assert.equal(findingsUrl("http://h:8000", "R1", { includeHidden: true }), "http://h:8000/runs/R1/findings?include_hidden=true");
  assert.equal(findingsUrl("http://h:8000", "R1", { includeHidden: false }), "http://h:8000/runs/R1/findings");
});

const noMapPayload = (over = {}) => ({
  ...good,
  coverage: { ...good.coverage, source_map: "none", sources_recovered: 0, ...over },
  findings: good.findings.map(f => ({ ...f, occurrences: (f.occurrences || []).map(o => ({ ...o, source_path: "input.js" })) })),
});

test("--nomap inverts the source-map checks: an honest no-map run passes", () => {
  assert.equal(scoreFindings(noMapPayload(), key).pass, false); // without the flag it can never pass
  const r = scoreFindings(noMapPayload(), key, { nomap: true });
  assert.equal(r.pass, true, JSON.stringify(r));
});

test("--nomap still fails a run that recovered source maps (a map run scored with the wrong flag)", () => {
  const r = scoreFindings(good, key, { nomap: true });
  assert.equal(r.pass, false);
  assert.equal(r.covFail.length, 3, JSON.stringify(r.covFail));
});

test("--nomap keeps every other gate: endpoints, params, secrets, the unattributed floor", () => {
  const p = noMapPayload();
  const drop = (pred) => ({ ...p, findings: p.findings.filter(f => !pred(f)) });
  assert.deepEqual(scoreFindings(drop(f => f.value === "GET /api/v1/profile"), key, { nomap: true }).missedEndpoints, ["ep-profile"]);
  assert.deepEqual(scoreFindings(drop(f => f.type === "param"), key, { nomap: true }).missedParams, ["ep-order:body:sku"]);
  assert.equal(scoreFindings(drop(f => f.value === "aws:ccc"), key, { nomap: true }).secretMisses.length, 1);
  assert.equal(scoreFindings(noMapPayload({ unattributed: 0 }), key, { nomap: true }).pass, false);
});

test("an expected endpoint hidden from the operator fails; an expect_hidden one does not", () => {
  const k = { ...key, should_find: key.should_find.map(e => (e.id === "ep-ga" ? { ...e, expect_hidden: true } : e)) };
  const visibleWithoutGa = { findings: good.findings.filter(f => !f.value.startsWith("GET /g/collect")) };
  assert.deepEqual(scoreFindings(good, k, { visible: visibleWithoutGa }).hiddenFromOperator, []);
  const visibleWithoutProfile = { findings: good.findings.filter(f => f.value !== "GET /api/v1/profile") };
  const r = scoreFindings(good, k, { visible: visibleWithoutProfile });
  assert.deepEqual(r.hiddenFromOperator, ["ep-profile"]);
  assert.equal(r.pass, false);
});

test("a runtime-observed request is not evidence of a recovered source file", () => {
  const observedOnly = { ...good, findings: good.findings.map(f => ({ ...f, occurrences: (f.occurrences || []).map(o => ({ ...o, source_path: o.source_path ? "capture-request://localhost/api/v1/orders" : o.source_path })) })) };
  assert.ok(scoreFindings(observedOnly, key).covFail.includes("no recovered source_path (all input.js)"));
  assert.ok(!scoreFindings(noMapPayload(), key, { nomap: true }).covFail.length);
});

