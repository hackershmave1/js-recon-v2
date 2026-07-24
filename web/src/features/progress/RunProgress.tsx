import { useEffect, useRef, useState } from "react";
import { useTenant } from "../../tenant/TenantContext";
import { streamRunEvents, type SseEvent } from "../../api/sseClient";
import { getFindings, getStatus, ApiError } from "../../api/apiClient";
import { TERMINAL_STATES, type FindingsResponse } from "../../api/types";

export function RunProgress({ runId, onFindings }: { runId: string; onFindings: (f: FindingsResponse) => void }) {
  const { tenantId } = useTenant();
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [state, setState] = useState<string>("…");
  const [stage, setStage] = useState<string | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const onFindingsRef = useRef(onFindings);
  onFindingsRef.current = onFindings;

  useEffect(() => {
    if (!tenantId) return;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const [s, f] = await Promise.all([getStatus(tenantId, runId), getFindings(tenantId, runId)]);
        if (controller.signal.aborted) return;
        setState(s.state); setStage(s.stage); setPct(s.pct);
        onFindingsRef.current(f);
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(e instanceof ApiError ? e.message : "Failed to load run");
      }
    };
    streamRunEvents(runId, tenantId, {
      signal: controller.signal,
      onOpen: () => { void refresh(); },
      onEvent: (e) => setEvents((prev) => [...prev, e]),
      checkTerminal: async () => {
        try {
          const s = await getStatus(tenantId, runId);
          if (controller.signal.aborted) return true;
          setState(s.state);
          if (TERMINAL_STATES.has(s.state)) { void refresh(); return true; }
          return false;
        } catch (e) {
          if (controller.signal.aborted) return true;
          setError(e instanceof ApiError ? e.message : "Failed to load run");
          return true;
        }
      },
      onFallback: () => { void refresh(); },
    });
    return () => controller.abort();
  }, [tenantId, runId]);

  return (
    <div className="card">
      <h2>Run {runId}</h2>
      <p>State: <strong>{state}</strong>{stage ? ` · ${stage}` : ""}{pct != null ? ` · ${pct}%` : ""}</p>
      {error && <p className="sev-high">{error}</p>}
      <ul>{events.map((e, i) => <li key={i} className="muted">{e.event}: {e.data}</li>)}</ul>
    </div>
  );
}
