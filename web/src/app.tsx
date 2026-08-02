import { useState } from "react";
import { useParams } from "react-router";
import { NewRunPanel } from "./features/newRun/NewRunPanel";
import { RunProgress } from "./features/progress/RunProgress";
import { FindingsPage } from "./features/findings/FindingsPage";
import { SourcesPage } from "./features/sources/SourcesPage";
import { ApiSpecPage } from "./features/apispec/ApiSpecPage";
import { ProbePanel } from "./features/probe/ProbePanel";
import { OverviewPanel } from "./features/overview/OverviewPanel";
import { DiscoveryEmpty } from "./features/discovery/DiscoveryEmpty";
import { Shell } from "./shell/Shell";
import { useTenant } from "./tenant/TenantContext";
import { TERMINAL_STATES, type FindingsResponse } from "./api/types";

export function RunWorkspace() {
  const { id } = useParams();
  const { tenantId } = useTenant();
  const [findings, setFindings] = useState<FindingsResponse | null>(null);
  const [state, setState] = useState<string | null>(null);
  if (!id) return null;
  const terminal = state != null && TERMINAL_STATES.has(state);
  // Each panel is wrapped in a <section id> matching a Sidebar nav item so the shell
  // can scroll to it. Render conditions are unchanged from before the shell landed.
  return (
    <Shell runId={id}>
      <section id="overview">
        {findings && <OverviewPanel data={findings} />}
        <RunProgress runId={id} onFindings={setFindings} onState={setState} />
        <DiscoveryEmpty runId={id} state={state} />
      </section>
      {tenantId && (
        <section id="sources">
          <SourcesPage data={findings} tenantId={tenantId} runId={id} />
        </section>
      )}
      {terminal && (
        <section id="api-spec">
          <ApiSpecPage data={findings} runId={id} />
        </section>
      )}
      {findings && (
        <section id="findings">
          <FindingsPage data={findings} runId={id} />
        </section>
      )}
      {terminal && (
        <section id="probe">
          <ProbePanel runId={id} />
        </section>
      )}
    </Shell>
  );
}

// New Recon is framed by the same shell as the rest of the app (sessions mode: no
// active run) so it carries the sidebar (Sessions nav + engagement switcher) and
// top bar — a consistent look and a way back out, not a dead-end standalone page.
export function Home() {
  return (
    <Shell mode="sessions">
      <NewRunPanel />
    </Shell>
  );
}
