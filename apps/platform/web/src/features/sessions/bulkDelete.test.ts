import { describe, it, expect, vi } from "vitest";
import { ApiError } from "../../api/apiClient";
import { deleteInBatches, selectedVisibleIds } from "./bulkDelete";

const ids = (n: number) => Array.from({ length: n }, (_, i) => `s${i}`);

describe("deleteInBatches", () => {
  it("sends a large selection as bounded batches, merges the results and reports progress", async () => {
    const batches: string[][] = [];
    const progress: number[] = [];
    const result = await deleteInBatches(
      ids(60),
      async (batch) => { batches.push(batch); return { deleted: batch, not_found: [], failed: [] }; },
      (done) => progress.push(done),
    );
    expect(batches.map((b) => b.length)).toEqual([25, 25, 10]);
    expect(progress).toEqual([25, 50, 60]);
    expect(result.deleted).toHaveLength(60);
  });

  it("a batch whose request fails marks only its ids failed and keeps going", async () => {
    const send = vi.fn()
      .mockRejectedValueOnce(new Error("504"))
      .mockResolvedValueOnce({ deleted: ["s25"], not_found: ["s26"], failed: [{ id: "s27", error: "RuntimeError" }] });
    const result = await deleteInBatches(ids(28), send, () => {});
    expect(send).toHaveBeenCalledTimes(2);
    expect(result.failed.map((f) => f.id)).toEqual([...ids(25), "s27"]);
    expect(result.deleted).toEqual(["s25"]);
    expect(result.not_found).toEqual(["s26"]);
  });
});

describe("deleteInBatches on a client error", () => {
  it("stops after a 4xx (e.g. signed out) instead of sending every remaining batch", async () => {
    const send = vi.fn().mockRejectedValue(new ApiError(401, "expired"));
    const result = await deleteInBatches(ids(60), send, () => {});
    expect(send).toHaveBeenCalledTimes(1);
    expect(result.failed).toHaveLength(60);
  });
});

describe("selectedVisibleIds", () => {
  it("never includes a selected session the current filter hides", () => {
    expect(selectedVisibleIds(new Set(["a", "b", "c"]), [{ session_id: "b" }, { session_id: "c" }, { session_id: "d" }]))
      .toEqual(["b", "c"]);
  });
});
