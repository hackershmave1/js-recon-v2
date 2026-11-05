import { useEffect, useState } from "react";
import { getSourcesIndexStatus } from "../../api/apiClient";
import type { SourcesIndexStatus } from "../../api/types";

const POLL_MS = 5000;
const MAX_POLL_MS = 10 * 60 * 1000;

// Background indexing of an OLDER run's bundles (search hits gain line numbers as it goes). Asking
// for the status is also what starts the job server-side, so the Sources page calls it on open and
// then polls this cheap endpoint — never the search itself, which can take seconds — while
// `indexing` is true. A failed read is silent: search works either way.
export function useIndexStatus(tenantId: string, runId: string): SourcesIndexStatus | null {
  const [status, setStatus] = useState<SourcesIndexStatus | null>(null);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    const poll = () => {
      getSourcesIndexStatus(tenantId, runId)
        .then((s) => {
          if (!live) return;
          setStatus(s);
          if (s.indexing && Date.now() - started < MAX_POLL_MS) timer = setTimeout(poll, POLL_MS);
        })
        .catch(() => {});
    };
    setStatus(null);
    poll();
    return () => { live = false; clearTimeout(timer); };
  }, [tenantId, runId]);
  return status;
}
