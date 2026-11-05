import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import * as api from "../../api/apiClient";
import { useIndexStatus } from "./useIndexStatus";

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const flush = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

describe("useIndexStatus", () => {
  it("polls while indexing and stops once done", async () => {
    const get = vi.spyOn(api, "getSourcesIndexStatus")
      .mockResolvedValueOnce({ indexed: 0, total: 3, indexing: true })
      .mockResolvedValueOnce({ indexed: 2, total: 3, indexing: true })
      .mockResolvedValue({ indexed: 3, total: 3, indexing: false });
    const { result } = renderHook(() => useIndexStatus("t", "r"));
    await flush();
    expect(result.current?.indexed).toBe(0);
    await flush(5000);
    expect(result.current?.indexed).toBe(2);
    await flush(5000);
    expect(result.current).toEqual({ indexed: 3, total: 3, indexing: false });
    await flush(20000);
    expect(get).toHaveBeenCalledTimes(3); // no polling after indexing ends
  });

  it("does not poll a fully indexed run", async () => {
    const get = vi.spyOn(api, "getSourcesIndexStatus").mockResolvedValue({ indexed: 5, total: 5, indexing: false });
    renderHook(() => useIndexStatus("t", "r"));
    await flush(30000);
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("a failed status read is silent (search still works)", async () => {
    vi.spyOn(api, "getSourcesIndexStatus").mockRejectedValue(new Error("down"));
    const { result } = renderHook(() => useIndexStatus("t", "r"));
    await flush(1000);
    expect(result.current).toBeNull();
  });
});
