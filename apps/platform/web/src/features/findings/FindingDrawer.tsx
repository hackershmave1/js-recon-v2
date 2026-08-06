import type { Finding, SourceJump } from "../../api/types";
import { FindingDetail } from "./FindingDetail";

// Right-side drawer holding one finding's full detail (design "Finding drawer").
// Reuses FindingDetail, so reveal + triage + occurrence redaction all come along
// unchanged. NOTE: manual-probe artifacts aren't folded in here yet — that needs
// matching a finding to its reconstructed request; it stays in the Probe section
// until a later pass.
// `onJumpToSource` is optional: the Findings page wires it (occurrences become
// jump-to-source buttons) but the shared ApiSpec drawer leaves it unset (plain text).
export function FindingDrawer({ finding, runId, onClose, onJumpToSource }: {
  finding: Finding | null;
  runId: string;
  onClose: () => void;
  onJumpToSource?: (j: SourceJump) => void;
}) {
  if (!finding) return null;
  return (
    <>
      <div className="fp-scrim" onClick={onClose} />
      <aside className="fp-drawer" role="dialog" aria-label="Finding detail">
        <div className="fp-drawer-head">
          <span className="fp-drawer-title">Finding detail</span>
          <button type="button" onClick={onClose}>Close</button>
        </div>
        <FindingDetail finding={finding} runId={runId} onJumpToSource={onJumpToSource} />
      </aside>
    </>
  );
}
