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
