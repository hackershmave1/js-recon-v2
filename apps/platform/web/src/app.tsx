import { useEffect } from "react";
import { Outlet, useLocation, useNavigate, useParams } from "react-router";
import { listSessions } from "./api/apiClient";
import { NewRunPanel } from "./features/newRun/NewRunPanel";
import { RunProgress } from "./features/progress/RunProgress";
import { RunDataProvider, useRunData } from "./features/progress/runData";
import { FindingsPage } from "./features/findings/FindingsPage";
import { SourcesPage } from "./features/sources/SourcesPage";
import { ApiSpecPage } from "./features/apispec/ApiSpecPage";
import { ProbePanel } from "./features/probe/ProbePanel";
import { TechPage } from "./features/tech/TechPage";
import { OverviewPanel } from "./features/overview/OverviewPanel";
import { DiscoveryEmpty } from "./features/discovery/DiscoveryEmpty";
import { Shell } from "./shell/Shell";
import { ErrorBoundary } from "./shell/ErrorBoundary";
import { useTenant } from "./tenant/TenantContext";
import { TERMINAL_STATES, type SourceJump } from "./api/types";

// The run workspace is a layout route: the SSE/findings engine is provided once here
// (keyed by run id so a run switch remounts it fresh — the monotonic-guard refs must
// not bleed across runs) and every page renders into the <Outlet> below it, so moving
// between a run's pages never tears the live stream down or refetches findings.
export function RunWorkspace() {
  const { id } = useParams();
  const location = useLocation();
  if (!id) return null;
  return (
    <RunDataProvider key={id} runId={id}>
      <Shell runId={id}>
        {/* Contain a page crash (and log it) instead of blanking the workspace;
            keyed by route so it resets when you navigate to another page. */}
        <ErrorBoundary key={location.pathname}>
          <Outlet />
        </ErrorBoundary>
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
  const { findings, technologies, state, failureCategory, failureReason, failureHost } = useRunData();
  const { id } = useParams();
  return (
    <>
      {findings && <OverviewPanel data={findings} technologies={technologies} />}
      <RunProgress />
      <DiscoveryEmpty
        runId={id!}
        state={state === "…" ? null : state}
        failureCategory={failureCategory}
        failureReason={failureReason}
        failureHost={failureHost}
      />
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

export function TechRoute() {
  const { technologies, loaded } = useRunData();
  if (!loaded) return <NotReady title="Loading…" body="Fetching this run's technologies." />;
  if (!technologies) return <NotReady title="No tech stack yet" body="Technologies appear here once analysis has run." />;
  return <TechPage data={technologies} />;
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
// The extension's "Open workspace" deep-links ?capture=<ext session id>. Resolve it to the
// operator's captured run: match the ext id against the session list's external_id and jump to
// that session's latest run (or the sessions list if it has no run / isn't in this tenant yet).
export function useCaptureDeepLink() {
  const navigate = useNavigate();
  const { search } = useLocation();
  const { tenantId } = useTenant();
  useEffect(() => {
    const ext = new URLSearchParams(search).get("capture");
    if (!ext || !tenantId) return;
    let live = true;
    listSessions(tenantId)
      .then((r) => {
        if (!live) return;
        const match = r.sessions.find((s) => s.external_id === ext);
        navigate(match?.latest_run ? `/runs/${match.latest_run.run_id}` : "/sessions", { replace: true });
      })
      .catch(() => { if (live) navigate("/sessions", { replace: true }); });
    return () => { live = false; };
  }, [search, tenantId, navigate]);
}

export function Home() {
  useCaptureDeepLink();
  return (
    <Shell mode="sessions">
      <NewRunPanel />
    </Shell>
  );
}
