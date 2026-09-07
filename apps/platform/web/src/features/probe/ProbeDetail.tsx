import type { ReconstructedRequest } from "../../api/types";
import { ArtifactTabs } from "./ArtifactTabs";
import { ParamsTable } from "./ParamsTable";
import { HostSelector } from "./HostSelector";
import { ProbeHandoff } from "./ProbeHandoff";

function isAbsolute(url: string | null): boolean {
  return !!url && url.includes("://");
}

export function ProbeDetail({
  req, runId, triageStatus, onTriaged, isShadow,
  inScopeHosts, target, host, useCustom, customHost,
  onPick, onCustom, onCommit,
}: {
  req: ReconstructedRequest;
  runId: string;
  triageStatus: string | null;
  onTriaged: (hashes: string[], status: string) => void;
  isShadow: boolean;
  inScopeHosts: string[];
  target: string | null;
  host: string;
  useCustom: boolean;
  customHost: string;
  onPick: (value: string) => void;
  onCustom: (value: string) => void;
  onCommit: () => void;
}) {
  const isRelative = !isAbsolute(req.example_url);

  return (
    <div className="pb-detail">
      <div className="pb-detail-header">
        <span className={"pb-method-badge pb-method-" + req.method.toLowerCase()}>{req.method}</span>
        <code className="pb-detail-path">{req.path}</code>
        {isShadow && <span className="pb-marker pb-marker-shadow">shadow</span>}
      </div>
      {!req.probeable && !req.artifacts && (
        <p className="muted pb-not-probeable-reason">
          This endpoint could not be fully reconstructed (unresolved base URL or non-HTTP sink) — listed for visibility.
        </p>
      )}
      {isRelative && (
        <HostSelector
          hosts={inScopeHosts} target={target} useCustom={useCustom}
          host={host} customHost={customHost}
          onPick={onPick} onCustom={onCustom} onCommit={onCommit}
        />
      )}
      <ArtifactTabs artifacts={req.artifacts} />
      <ParamsTable req={req} />
      <ProbeHandoff req={req} runId={runId} triageStatus={triageStatus} onTriaged={onTriaged} />
    </div>
  );
}
