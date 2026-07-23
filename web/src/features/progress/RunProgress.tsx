import { useEffect, useRef, useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { streamRunEvents, type SseEvent } from "../../api/sseClient";
import { getFindings, getStatus } from "../../api/apiClient";
import { TERMINAL_STATES, type FindingsResponse } from "../../api/types";

export function RunProgress({ runId, onFindings }: { runId: string; onFindings: (f: FindingsResponse) => void }) {
  const { tenantId } = useTenant();
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [state, setState] = useState<string>("…");
  const [stage, setStage] = useState<string | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const onFindingsRef = useRef(onFindings);
  onFindingsRef.current = onFindings;

  useEffect(() => {
    if (!tenantId) return;
    const controller = new AbortController();
    const refresh = async () => {
      const [s, f] = await Promise.all([getStatus(tenantId, runId), getFindings(tenantId, runId)]);
      setState(s.state); setStage(s.stage); setPct(s.pct);
      onFindingsRef.current(f);
    };
    streamRunEvents(runId, tenantId, {
      signal: controller.signal,
      onOpen: () => { void refresh(); },
      onEvent: (e) => setEvents((prev) => [...prev, e]),
      checkTerminal: async () => {
        const s = await getStatus(tenantId, runId);
        setState(s.state);
        if (TERMINAL_STATES.has(s.state)) { void refresh(); return true; }
        return false;
      },
      onFallback: () => { void refresh(); },
    });
    return () => controller.abort();
  }, [tenantId, runId]);

  return (
    <div className="card">
      <h2>Run {runId}</h2>
      <p>State: <strong>{state}</strong>{stage ? ` · ${stage}` : ""}{pct != null ? ` · ${pct}%` : ""}</p>
      <ul>{events.map((e, i) => <li key={i} className="muted">{e.event}: {e.data}</li>)}</ul>
    </div>
  );
}
