import { useEffect, useState } from "react";
import { getServerHealth } from "../../api/apiClient";

// Read-only status of the stack's dev-only local-targets switch (RECON_ALLOW_LOCAL_EGRESS).
// It is a server setting, not a per-user one, so this card only reports it and says how to change it.
export function LocalTargetsCard() {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    getServerHealth().then((h) => { if (live) setAllowed(h.allowLocalTargets === true); }).catch(() => { if (live) setAllowed(null); });
    return () => { live = false; };
  }, []);
  return (
    <section className="card settings-card" aria-labelledby="local-targets-title">
      <h2 id="local-targets-title" className="rp-title">Local development targets</h2>
      {allowed === null ? (
        <p className="muted">Status unavailable.</p>
      ) : (
        <>
          <p>
            <strong>{allowed ? "Enabled" : "Disabled"}</strong>
            {allowed
              ? " — localhost can be crawled from New Recon and counts as declared scope."
              : " — extension captures of localhost still upload and analyze; localhost just can't be crawled from New Recon or marked in scope."}
          </p>
          <p className="muted">
            A server setting for development stacks: start the stack with <code>RECON_ALLOW_LOCAL_EGRESS=true</code> to
            {allowed ? " keep it on (leave it off in production)." : " turn it on. Leave it off in production."}
          </p>
        </>
      )}
    </section>
  );
}
