import { useEffect, useRef } from "react";
import type { Finding, SpecSummary } from "../../api/types";
import { SpecUpload } from "../findings/SpecUpload";
import { BaseUrlPanel } from "../findings/BaseUrlPanel";
import { WrapperPanel } from "../findings/WrapperPanel";
import { useResizableRail } from "../../shell/useResizableRail";
import { Icon } from "../../shell/icons";
import { useTuningRail, type TuningLever } from "./TuningRailContext";
import { useRunDataOptional } from "../progress/runData";
import "./tuning.css";

// REQ-TU2: if a single unresolved base covers ≥40% of unattributed, recommend base-URL lever.
function recommendLever(findings: Finding[], unattributed: number): TuningLever | null {
  if (unattributed === 0) return null;
  const baseCounts = new Map<string, number>();
  for (const f of findings) {
    if (f.type !== "endpoint_unresolved" && f.type !== "endpoint_suspected") continue;
    const val = f.value ?? f.path ?? "";
    const m = val.match(/^(:[\w]+|\{[\w]+\})/);
    if (m) baseCounts.set(m[1], (baseCounts.get(m[1]) ?? 0) + 1);
  }
  for (const count of baseCounts.values()) {
    if (count / unattributed >= 0.4) return "base-url";
  }
  return null;
}

export function TuningRail({ runId, specSummary }: {
  runId: string;
  specSummary: SpecSummary | null;
}) {
  const { isOpen, focusedLever, close } = useTuningRail();
  const { width, collapsed, toggleCollapsed, resizerProps } = useResizableRail("tuning");
  const runData = useRunDataOptional();
  const findings = runData?.findings ?? null;
  const refreshFindings = runData?.refreshFindings;

  const c = findings?.coverage ?? null;
  const unattributed = c?.unattributed ?? 0;
  const total = (c?.attributed ?? 0) + unattributed;
  const unattributedPct = total > 0 ? Math.round((unattributed / total) * 100) : 0;
  const recommended = recommendLever(findings?.findings ?? [], unattributed);

  // REQ-TU3: when programmatically opened (e.g. from RunHeader nudge), ensure rail is visible.
  useEffect(() => {
    if (isOpen && collapsed) toggleCollapsed();
  }, [isOpen, collapsed, toggleCollapsed]);

  const specRef = useRef<HTMLDivElement>(null);
  const baseUrlRef = useRef<HTMLDivElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!focusedLever || collapsed) return;
    const refMap: Record<TuningLever, React.RefObject<HTMLDivElement | null>> = {
      spec: specRef,
      "base-url": baseUrlRef,
      wrapper: wrapperRef,
    };
    refMap[focusedLever].current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [focusedLever, collapsed]);

  function handleToggle() {
    if (!collapsed) close();
    toggleCollapsed();
  }

  return (
    <>
      {!collapsed && (
        <div className="tu-resizer" role="separator" aria-orientation="vertical"
          aria-label="Resize tuning rail" title="Drag to resize" {...resizerProps} />
      )}
      <aside
        className={"tu-rail" + (collapsed ? " tu-rail-collapsed" : "")}
        style={!collapsed ? { width, flexBasis: width } : undefined}
        aria-label="Extraction tuning"
      >
        <div className="tu-rail-head">
          {collapsed ? (
            <button type="button" className="tu-toggle" onClick={handleToggle}
              title="Open tuning rail" aria-label="Open extraction tuning rail">
              <Icon name="panel" size={15} />
            </button>
          ) : (
            <>
              <h2 className="tu-rail-title">Tune extraction</h2>
              <button type="button" className="tu-toggle" onClick={handleToggle}
                title="Collapse tuning rail" aria-label="Collapse tuning rail">
                <Icon name="panel" size={15} />
              </button>
            </>
          )}
        </div>

        {!collapsed && (
          <>
            {unattributed > 0 && (
              <div className="tu-banner" role="status">
                <span className="tu-banner-count">
                  {unattributed} ({unattributedPct}%) unattributed
                </span>
                {recommended === "base-url" && (
                  <span className="tu-banner-tip">A base-URL rule may resolve these</span>
                )}
              </div>
            )}

            <div className="tu-levers">
              <div ref={specRef} className="tu-lever">
                <SpecUpload runId={runId} initialSummary={specSummary} onApplied={refreshFindings ?? undefined} />
              </div>
              <div
                ref={baseUrlRef}
                className={"tu-lever" + (recommended === "base-url" ? " tu-lever-recommended" : "")}
              >
                {recommended === "base-url" && (
                  <span className="tu-lever-badge" aria-label="Recommended lever">Recommended</span>
                )}
                <BaseUrlPanel runId={runId} onApplied={refreshFindings ?? undefined} />
              </div>
              <div ref={wrapperRef} className="tu-lever">
                <WrapperPanel runId={runId} onApplied={refreshFindings ?? undefined} />
              </div>
            </div>
          </>
        )}
      </aside>
    </>
  );
}
