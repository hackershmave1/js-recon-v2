import { TERMINAL_STATES } from "./types";

export interface SseEvent { id: string; event: string; data: string; }

export interface SseHandlers {
  onEvent: (e: SseEvent) => void;
  onOpen?: () => void;                    // fetch findings/status on (re)connect
  checkTerminal: () => Promise<boolean>;  // on clean close: terminal? stop : reconnect
  onFallback?: () => void;                // after 3 network errors → poll
  signal?: AbortSignal;
}

function parseFrame(frame: string): SseEvent | null {
  if (!frame.trim() || frame.startsWith(":")) return null; // keep-alive / comment
  let id = "", event = "message", data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  return { id, event, data };
}

function isTerminal(e: SseEvent): boolean {
  if (e.event !== "run.transition") return false;
  try { return TERMINAL_STATES.has(JSON.parse(e.data)?.to); } catch { return false; }
}

export async function streamRunEvents(runId: string, tenantId: string, h: SseHandlers): Promise<void> {
  let lastId: string | null = null;
  let failures = 0;
  while (!h.signal?.aborted) {
    try {
      const headers: Record<string, string> = { "X-Tenant-Id": tenantId, Accept: "text/event-stream" };
      if (lastId) headers["Last-Event-ID"] = lastId;
      const res = await fetch(`/runs/${encodeURIComponent(runId)}/events`, { headers, signal: h.signal });
      if (!res.ok || !res.body) throw new Error(`sse ${res.status}`);
      failures = 0;
      h.onOpen?.();
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let terminal = false;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const evt = parseFrame(buf.slice(0, idx));
          buf = buf.slice(idx + 2);
          if (!evt) continue;
          if (evt.id) lastId = evt.id;
          h.onEvent(evt);
          if (isTerminal(evt)) terminal = true;
        }
        if (terminal) return; // authoritative terminal: stop, no reconnect
      }
      // clean close with no terminal event: stop iff the run is already terminal
      if (await h.checkTerminal()) return;
      // else loop → reconnect (server caps a stream at 300s; keep watching)
    } catch (err) {
      if (h.signal?.aborted) return;
      failures += 1;
      if (failures >= 3) { h.onFallback?.(); return; }
    }
  }
}
