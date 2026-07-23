import { describe, it, expect, vi, beforeEach } from "vitest";
import { streamRunEvents } from "./sseClient";

beforeEach(() => vi.restoreAllMocks());

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(ctrl) { if (i < chunks.length) ctrl.enqueue(enc.encode(chunks[i++])); else ctrl.close(); },
  });
}
const ok = (chunks: string[]) => ({ ok: true, status: 200, body: streamOf(chunks) });

describe("sseClient", () => {
  it("parses a frame split mid-line across chunks", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      ok(['id: 1\nevent: analyze.coverage\nda', 'ta: {"secrets":2}\n\n'])));
    const events: unknown[] = [];
    await streamRunEvents("r", "t", {
      onEvent: (e) => events.push(e),
      checkTerminal: async () => true,
    });
    expect(events).toEqual([{ id: "1", event: "analyze.coverage", data: '{"secrets":2}' }]);
  });

  it("ignores keep-alive comment lines", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok([": keep-alive\n\n"])));
    const events: unknown[] = [];
    await streamRunEvents("r", "t", { onEvent: (e) => events.push(e), checkTerminal: async () => true });
    expect(events).toEqual([]);
  });

  it("stops on a terminal transition without reconnecting", async () => {
    const f = vi.fn().mockResolvedValue(
      ok(['id: 9\nevent: run.transition\ndata: {"from":"analyzing","to":"done"}\n\n']));
    vi.stubGlobal("fetch", f);
    const checkTerminal = vi.fn(async () => false);
    await streamRunEvents("r", "t", { onEvent: () => {}, checkTerminal });
    expect(f).toHaveBeenCalledTimes(1);
    expect(checkTerminal).not.toHaveBeenCalled();
  });

  it("on clean close checks status; reconnects only if not terminal", async () => {
    const f = vi.fn()
      .mockResolvedValueOnce(ok(["id: 1\nevent: run.progress\ndata: {}\n\n"]))
      .mockResolvedValueOnce(ok([": keep-alive\n\n"]));
    vi.stubGlobal("fetch", f);
    const checkTerminal = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    await streamRunEvents("r", "t", { onEvent: () => {}, checkTerminal });
    expect(f).toHaveBeenCalledTimes(2);
  });

  it("falls back after 3 consecutive network errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network")));
    const onFallback = vi.fn();
    await streamRunEvents("r", "t", { onEvent: () => {}, checkTerminal: async () => false, onFallback });
    expect(onFallback).toHaveBeenCalledOnce();
  });

  it("sends the badge and Last-Event-ID on reconnect", async () => {
    const f = vi.fn()
      .mockResolvedValueOnce(ok(["id: 42\nevent: run.progress\ndata: {}\n\n"]))
      .mockResolvedValueOnce(ok([": keep-alive\n\n"]));
    vi.stubGlobal("fetch", f);
    const checkTerminal = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    await streamRunEvents("r", "t", { onEvent: () => {}, checkTerminal });
    expect(f.mock.calls[0][1].headers["X-Tenant-Id"]).toBe("t");
    expect(f.mock.calls[1][1].headers["Last-Event-ID"]).toBe("42");
  });
});
