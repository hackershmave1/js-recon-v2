import type { Finding } from "../../api/types";
import { explainFinding } from "./explainFinding";

// The four plain-language answers under a finding's header: what it is, how sure the tool is,
// why it has its severity, what to do next. A section with nothing supported to say is omitted.
export function FindingExplain({ finding }: { finding: Finding }) {
  const e = explainFinding(finding);
  return (
    <div className="fd-explain">
      {e.kind && (
        <section aria-label="What it is"><h4>What it is</h4><p>{e.kind}</p></section>
      )}
      {e.confidence.length > 0 && (
        <section aria-label="How sure the tool is">
          <h4>How sure the tool is</h4>
          <ul>{e.confidence.map((line) => <li key={line}>{line}</li>)}</ul>
        </section>
      )}
      {e.severityReason && (
        <section aria-label="Why this severity"><h4>Why this severity</h4><p>{e.severityReason}</p></section>
      )}
      {e.nextSteps.length > 0 && (
        <section aria-label="What to do next">
          <h4>What to do next</h4>
          <ul>{e.nextSteps.map((line) => <li key={line}>{line}</li>)}</ul>
        </section>
      )}
    </div>
  );
}
