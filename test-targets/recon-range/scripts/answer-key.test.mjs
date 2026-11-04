import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const key = JSON.parse(await readFile(new URL("../answer-key.json", import.meta.url)));

test("ids unique across should_find + blind_spots", () => {
  const ids = [...key.should_find, ...key.known_blind_spots].map(x => x.id);
  assert.equal(new Set(ids).size, ids.length);
});
test("each should_find has method and (operation or host)", () => {
  for (const e of key.should_find) {
    assert.ok(e.method, `${e.id} missing method`);
    assert.ok(e.operation || e.host, `${e.id} needs operation or host`);
  }
});
test("secrets.must has positive counts", () => {
  for (const [p, n] of Object.entries(key.secrets.must)) assert.ok(n >= 1, `${p} must>=1`);
});

test("the graduated cross-module call is scored by host + operation", async () => {
  const { scoreFindings } = await import("./score.mjs");
  assert.ok(!key.known_blind_spots.some(b => b.id === "bs-crosschunk"), "graduated: no longer a blind spot");
  const payload = (host) => ({ coverage: {}, findings: [{ type: "endpoint", value: "GET /api/v3/orders", occurrences: [{ host }] }] });
  assert.ok(!scoreFindings(payload("api.acme.com"), key).missedEndpoints.includes("bs-crosschunk"), "resolved with its host: found");
  assert.ok(scoreFindings(payload(null), key).missedEndpoints.includes("bs-crosschunk"), "path only (base lost): missed");
});

test("only the planted analytics/vendor calls may be hidden from the operator", () => {
  assert.deepEqual(key.should_find.filter(e => e.expect_hidden).map(e => e.id).sort(), ["ep-ga", "ep-sentry", "ep-stripe"]);
});

