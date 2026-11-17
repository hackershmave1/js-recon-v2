import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, deleteEngagement, listEngagements } from "../../api/apiClient";
import type { Engagement } from "../../api/types";
import { useTenant } from "../../tenant/TenantContext";
import { Icon } from "../../shell/icons";
import { useEngagementFilter } from "./engagementFilter";
import { NewEngagementModal } from "./NewEngagementModal";
import { DeleteEngagementConfirm } from "./DeleteEngagementConfirm";

// The sidebar engagement switcher (design mockup lines 62-72): shows the active
// engagement (or "All engagements") and a menu to switch or create one. Selecting an
// engagement filters the Sessions grid and becomes the default umbrella for new runs. Each
// engagement can also be deleted from here (#262): its sessions move to "No engagement".
export function EngagementSwitcher() {
  const { tenantId } = useTenant();
  const { engagementId, setEngagementId } = useEngagementFilter();
  const [engagements, setEngagements] = useState<Engagement[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [doomed, setDoomed] = useState<Engagement | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  // Only the latest load may land: a slow earlier list must not overwrite a newer one (create,
  // then delete, in quick succession).
  const latestLoad = useRef(0);
  const reload = useCallback(() => {
    if (!tenantId) return;
    const load = ++latestLoad.current;
    listEngagements(tenantId)
      .then((r) => {
        if (load !== latestLoad.current) return;
        setEngagements(r.engagements);
        setLoaded(true);
      })
      .catch(() => {
        if (load !== latestLoad.current) return;
        setEngagements([]); // switcher degrades to "All engagements"
        setLoaded(false); // ...but a failed load must not clear the selection (see below)
      });
  }, [tenantId]);

  useEffect(() => { reload(); }, [reload]);

  // A selection on an engagement that no longer exists (deleted here, in another tab, or a
  // stale localStorage id) falls back to All engagements: filtering on it shows an empty grid,
  // and New Run would try to attach a new session to it. Only after a SUCCESSFUL load: a failed
  // one says nothing about whether the engagement exists.
  const stale = loaded && engagementId !== null
    && !engagements.some((e) => e.engagement_id === engagementId);
  useEffect(() => { if (stale) setEngagementId(null); }, [stale, setEngagementId]);

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
  // An engagement has no scope of its own (the session does), so the subtitle never prints one.
  const scopeLine = active ? "engagement" : `${engagements.length} engagement${engagements.length === 1 ? "" : "s"}`;

  function pick(id: string | null) {
    setEngagementId(id);
    setOpen(false);
  }

  function askDelete(engagement: Engagement) {
    setDeleteError(null);
    setDoomed(engagement);
    setOpen(false);
  }

  async function confirmDelete() {
    if (!tenantId || !doomed) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteEngagement(tenantId, doomed.engagement_id);
    } catch (err) {
      // A 404 means it is already gone (another tab): refreshing shows the truth.
      if (!(err instanceof ApiError && err.status === 404)) {
        setDeleteError(err instanceof ApiError ? err.message : "Failed to delete the engagement");
        setDeleting(false);
        return;
      }
    }
    setDeleting(false);
    setDoomed(null);
    reload(); // drops it from the menu and falls a selection on it back to All engagements
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
            <div key={e.engagement_id} className="eng-switch-row">
              <button type="button" role="menuitem"
                className={"eng-switch-opt" + (e.engagement_id === engagementId ? " sel" : "")}
                onClick={() => pick(e.engagement_id)}>
                <span className="eng-switch-opt-name">{e.name}</span>
              </button>
              <button type="button" role="menuitem" className="eng-switch-del" title="Delete engagement"
                aria-label={`Delete engagement ${e.name}`} onClick={() => askDelete(e)}>
                <Icon name="trash" size={13} />
              </button>
            </div>
          ))}
          <button type="button" className="eng-switch-new"
            onClick={() => { setModalOpen(true); setOpen(false); }}>
            <Icon name="plus" size={13} /> New engagement
          </button>
        </div>
      )}
      {doomed && (
        <DeleteEngagementConfirm engagement={doomed} busy={deleting} error={deleteError}
          onConfirm={confirmDelete}
          onCancel={() => { if (!deleting) setDoomed(null); }} />
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
