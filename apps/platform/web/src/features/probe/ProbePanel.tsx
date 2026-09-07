import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { useTenant } from "../../tenant/TenantContext";
import { useRunDataOptional } from "../progress/runData";
import { getRequests, ApiError } from "../../api/apiClient";
import type { ReconstructedRequest, RequestsResponse } from "../../api/types";
import { TuningRail } from "../tuning/TuningRail";
import { ProbeList } from "./ProbeList";
import { ProbeDetail } from "./ProbeDetail";
import { CUSTOM_SENTINEL } from "./HostSelector";
import "./probe.css";

function hostOf(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try { return new URL(raw).host || null; } catch { return raw || null; }
}

export function ProbePanel({ runId }: { runId: string }) {
  const { tenantId } = useTenant();
  const runData = useRunDataOptional();
  const [searchParams, setSearchParams] = useSearchParams();

  // In-scope discovered hosts, crawl target first — best-effort from run context.
  const target = hostOf(runData?.assets?.domain);
  const inScopeHosts = useMemo(() => {
    const names = (runData?.hosts?.hosts ?? []).filter((h) => h.in_scope).map((h) => h.host);
    const uniq = Array.from(new Set(names));
    uniq.sort((a, b) => (a === target ? -1 : b === target ? 1 : a.localeCompare(b)));
    return uniq;
  }, [runData?.hosts, target]);

  // Shadow classification: finding_hashes where spec_status.status === "shadow".
  // When findings aren't loaded yet, the set is empty and no badge is shown.
  const shadowHashes = useMemo(() => {
    const hashes = (runData?.findings?.findings ?? [])
      .filter((f) => f.spec_status?.status === "shadow")
      .map((f) => f.finding_hash);
    return new Set(hashes);
  }, [runData?.findings]);

  // Host resolver state — commits on blur/Enter to avoid per-keystroke refetch.
  const [host, setHost] = useState("");
  const [useCustom, setUseCustom] = useState(false);
  const [customHost, setCustomHost] = useState("");
  const [appliedCustom, setAppliedCustom] = useState("");
  const selected = host || (inScopeHosts[0] ?? "");
  const effectiveHost = useCustom ? appliedCustom : selected;

  const [data, setData] = useState<RequestsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    let live = true;
    getRequests(tenantId, runId, effectiveHost || undefined)
      .then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e) => { if (live) setError(e instanceof ApiError ? e.message : "Failed to load requests"); });
    return () => { live = false; };
  }, [tenantId, runId, effectiveHost]);

  const requests = useMemo(() => data?.requests ?? [], [data]);

  // Selection — URL-synced via ?op= (REQ-PB1); falls back to first probeable op.
  const opParam = searchParams.get("op");
  const selectedOp = useMemo(() => {
    if (opParam && requests.some((r) => r.operation === opParam)) return opParam;
    return requests.find((r) => r.probeable)?.operation ?? requests[0]?.operation ?? null;
  }, [opParam, requests]);

  function selectOp(op: string) {
    setSearchParams(
      (prev) => { const next = new URLSearchParams(prev); next.set("op", op); return next; },
      { replace: true },
    );
  }

  // Optimistic triage overlay: hash -> status; reflected immediately in detail.
  const [triageMap, setTriageMap] = useState<Map<string, string>>(new Map());
  function onTriaged(hashes: string[], status: string) {
    setTriageMap((prev) => {
      const next = new Map(prev);
      for (const h of hashes) next.set(h, status);
      return next;
    });
  }

  function triageStatusFor(req: ReconstructedRequest): string | null {
    for (const h of req.endpoint_hashes) {
      const s = triageMap.get(h);
      if (s) return s;
    }
    return null;
  }

  function onPick(value: string) {
    if (value === CUSTOM_SENTINEL) { setUseCustom(true); return; }
    setUseCustom(false);
    setHost(value);
  }

  if (error) {
    return (
      <div className="probe-outer">
        <div className="card probe-content">
          <h3>Manual probe</h3>
          <p className="sev-high">{error}</p>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="probe-outer">
        <div className="probe-content pb-skeleton" aria-busy="true" aria-label="Loading probe surface" />
      </div>
    );
  }

  if (data.count === 0) {
    return (
      <div className="probe-outer">
        <div className="card probe-content">
          <h3>Manual probe</h3>
          <p className="muted">No probeable requests reconstructed.</p>
        </div>
      </div>
    );
  }

  const selectedReq = requests.find((r) => r.operation === selectedOp) ?? null;

  return (
    <div className="probe-outer">
      <div className="probe-content probe-master-detail">
        <div className="pb-list-pane">
          <h3 className="pb-list-pane-title">
            Probe <span className="muted">({data.count})</span>
          </h3>
          <ProbeList
            requests={requests}
            selectedOp={selectedOp}
            shadowHashes={shadowHashes}
            onSelect={selectOp}
          />
        </div>
        <div className="pb-detail-pane">
          {selectedReq ? (
            <ProbeDetail
              req={selectedReq}
              runId={runId}
              triageStatus={triageStatusFor(selectedReq)}
              onTriaged={onTriaged}
              isShadow={selectedReq.endpoint_hashes.some((h) => shadowHashes.has(h))}
              inScopeHosts={inScopeHosts}
              target={target}
              host={selected}
              useCustom={useCustom}
              customHost={customHost}
              onPick={onPick}
              onCustom={setCustomHost}
              onCommit={() => setAppliedCustom(customHost.trim())}
            />
          ) : (
            <p className="muted pb-no-selection">Select a request from the list.</p>
          )}
        </div>
      </div>
      <TuningRail runId={runId} specSummary={null} />
    </div>
  );
}
