import { useCallback, useEffect, useRef, useState } from "react";
import { listEngagements } from "../../api/apiClient";
import type { Engagement } from "../../api/types";
import { useTenant } from "../../tenant/TenantContext";
import { Icon } from "../../shell/icons";
import { useEngagementFilter } from "./engagementFilter";
import { NewEngagementModal } from "./NewEngagementModal";

// The sidebar engagement switcher (design mockup lines 62-72): shows the active
// engagement (or "All engagements") and a menu to switch or create one. Selecting an
// engagement filters the Sessions grid and becomes the default umbrella for new runs.
export function EngagementSwitcher() {
  const { tenantId } = useTenant();
  const { engagementId, setEngagementId } = useEngagementFilter();
  const [engagements, setEngagements] = useState<Engagement[]>([]);
  const [open, setOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const reload = useCallback(() => {
    if (!tenantId) return;
    listEngagements(tenantId)
      .then((r) => setEngagements(r.engagements))
      .catch(() => setEngagements([])); // switcher degrades to "All engagements"
  }, [tenantId]);

  useEffect(() => { reload(); }, [reload]);

  // Close the menu on an outside click.
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const active = engagements.find((e) => e.engagement_id === engagementId) ?? null;
  const scopeLine = active
    ? active.in_scope_domains.join(", ") || "no scope set"
    : `${engagements.length} engagement${engagements.length === 1 ? "" : "s"}`;

  function pick(id: string | null) {
    setEngagementId(id);
    setOpen(false);
  }

  return (
    <div className="eng-switch" ref={ref}>
      <button type="button" className="eng-switch-btn" onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu" aria-expanded={open}>
        <span className="eng-switch-mark"><Icon name="switch" size={15} /></span>
        <span className="eng-switch-body">
          <span className="eng-switch-name">{active ? active.name : "All engagements"}</span>
          <span className="eng-switch-scope">{scopeLine}</span>
        </span>
        <span className="eng-switch-caret"><Icon name="chevron" size={13} /></span>
      </button>
      {open && (
        <div className="eng-switch-menu" role="menu">
          <button type="button" role="menuitem"
            className={"eng-switch-opt" + (engagementId === null ? " sel" : "")}
            onClick={() => pick(null)}>All engagements</button>
          {engagements.map((e) => (
            <button key={e.engagement_id} type="button" role="menuitem"
              className={"eng-switch-opt" + (e.engagement_id === engagementId ? " sel" : "")}
              onClick={() => pick(e.engagement_id)}>
              <span className="eng-switch-opt-name">{e.name}</span>
              {e.in_scope_domains.length > 0 && (
                <span className="eng-switch-opt-scope">{e.in_scope_domains.join(", ")}</span>
              )}
            </button>
          ))}
          <button type="button" className="eng-switch-new"
            onClick={() => { setModalOpen(true); setOpen(false); }}>
            <Icon name="plus" size={13} /> New engagement
          </button>
        </div>
      )}
      {modalOpen && tenantId && (
        <NewEngagementModal
          tenantId={tenantId}
          onClose={() => setModalOpen(false)}
          onCreated={(created) => {
            setModalOpen(false);
            reload();
            setEngagementId(created.engagement_id);
          }}
        />
      )}
    </div>
  );
}
