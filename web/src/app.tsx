import { useState } from "react";
import { useParams } from "react-router";
import { NewRunPanel } from "./features/newRun/NewRunPanel";
import { RunProgress } from "./features/progress/RunProgress";
import { FindingsView } from "./features/findings/FindingsView";
import type { FindingsResponse } from "./api/types";

export function RunWorkspace() {
  const { id } = useParams();
  const [findings, setFindings] = useState<FindingsResponse | null>(null);
  if (!id) return null;
  return (
    <div>
      <RunProgress runId={id} onFindings={setFindings} />
      {findings && <FindingsView data={findings} runId={id} />}
    </div>
  );
}

export function Home() { return <NewRunPanel />; }
