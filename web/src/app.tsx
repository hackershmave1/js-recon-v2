import { useState } from "react";
import { useParams } from "react-router";
import { NewRunPanel } from "./features/newRun/NewRunPanel";
import { RunProgress } from "./features/progress/RunProgress";
import { FindingsView } from "./features/findings/FindingsView";
import { AssetsInventory } from "./features/discovery/AssetsInventory";
import { ExportSpecButton } from "./features/export/ExportSpecButton";
import { ProbePanel } from "./features/probe/ProbePanel";
import { useTenant } from "./tenant/TenantContext";
import { TERMINAL_STATES, type FindingsResponse } from "./api/types";

export function RunWorkspace() {
  const { id } = useParams();
  const { tenantId } = useTenant();
  const [findings, setFindings] = useState<FindingsResponse | null>(null);
  const [state, setState] = useState<string | null>(null);
  if (!id) return null;
  const terminal = state != null && TERMINAL_STATES.has(state);
  return (
    <div>
      <RunProgress runId={id} onFindings={setFindings} onState={setState} />
      {tenantId && <AssetsInventory tenantId={tenantId} runId={id} />}
      {terminal && <ExportSpecButton runId={id} />}
      {findings && <FindingsView data={findings} runId={id} />}
      {terminal && <ProbePanel runId={id} />}
    </div>
  );
}

export function Home() { return <NewRunPanel />; }
