import { Outlet, useLocation, useNavigate, useParams } from "react-router";
import { NewRunPanel } from "./features/newRun/NewRunPanel";
import { RunProgress } from "./features/progress/RunProgress";
import { RunDataProvider, useRunData } from "./features/progress/runData";
import { FindingsPage } from "./features/findings/FindingsPage";
import { SourcesPage } from "./features/sources/SourcesPage";
import { ApiSpecPage } from "./features/apispec/ApiSpecPage";
import { ProbePanel } from "./features/probe/ProbePanel";
import { OverviewPanel } from "./features/overview/OverviewPanel";
import { DiscoveryEmpty } from "./features/discovery/DiscoveryEmpty";
import { Shell } from "./shell/Shell";
import { useTenant } from "./tenant/TenantContext";
import { TERMINAL_STATES, type SourceJump } from "./api/types";

// The run workspace is a layout route: the SSE/findings engine is provided once here
// (keyed by run id so a run switch remounts it fresh — the monotonic-guard refs must
// not bleed across runs) and every page renders into the <Outlet> below it, so moving
// between a run's pages never tears the live stream down or refetches findings.
export function RunWorkspace() {
  const { id } = useParams();
  if (!id) return null;
  return (
    <RunDataProvider key={id} runId={id}>
      <Shell runId={id}>
        <Outlet />
      </Shell>
    </RunDataProvider>
  );
}

// Shown when a page is opened before its data exists (the spec/probe before a run is
// terminal, or findings before analyze emits) so a directly-navigated page reads as
// "not ready yet" rather than blank or crashing.
function NotReady({ title, body }: { title: string; body: string }) {
  return (
    <div className="card">
      <h2 className="rp-title">{title}</h2>
      <p className="muted">{body}</p>
    </div>
  );
}

export function OverviewRoute() {
  const { findings, state } = useRunData();
  const { id } = useParams();
  return (
    <>
      {findings && <OverviewPanel data={findings} />}
      <RunProgress />
      <DiscoveryEmpty runId={id!} state={state === "…" ? null : state} />
    </>
  );
}

export function SourcesRoute() {
  const { findings } = useRunData();
  const { id } = useParams();
  const { tenantId } = useTenant();
  const location = useLocation();
  // A Findings→Sources jump navigates here carrying the target occurrence in router
  // state (in-memory, one-shot); a plain visit has no state and shows the first file.
  const jump = (location.state as { jump?: SourceJump } | null)?.jump ?? null;
  if (!tenantId) return null;
  return <SourcesPage data={findings} tenantId={tenantId} runId={id!} jump={jump} />;
}

export function FindingsRoute() {
  const { findings, loaded } = useRunData();
  const { id } = useParams();
  const navigate = useNavigate();
  if (!loaded) return <NotReady title="Loading…" body="Fetching this run's findings." />;
  if (!findings) return <NotReady title="No findings yet" body="Findings appear here once analysis has run." />;
  return (
    <FindingsPage
      data={findings}
      runId={id!}
      onJumpToSource={(j) => navigate(`/runs/${id}/sources`, { state: { jump: j } })}
    />
  );
}

export function ApiSpecRoute() {
  const { findings, state, loaded } = useRunData();
  const { id } = useParams();
  if (!loaded) return <NotReady title="Loading…" body="Fetching this run's status." />;
  if (!TERMINAL_STATES.has(state)) {
    return <NotReady title="API spec pending" body="The reconstructed API surface is available once the run completes." />;
  }
  return <ApiSpecPage data={findings} runId={id!} />;
}

export function ProbeRoute() {
  const { state, loaded } = useRunData();
  const { id } = useParams();
  if (!loaded) return <NotReady title="Loading…" body="Fetching this run's status." />;
  if (!TERMINAL_STATES.has(state)) {
    return <NotReady title="Manual probe pending" body="Reconstructed requests are probeable once the run completes." />;
  }
  return <ProbePanel runId={id!} />;
}

// New Recon is framed by the same shell as the rest of the app (sessions mode: no
// active run) so it carries the sidebar (Sessions nav + engagement switcher) and top
// bar — a consistent look and a way back out, not a dead-end standalone page.
export function Home() {
  return (
    <Shell mode="sessions">
      <NewRunPanel />
    </Shell>
  );
}
