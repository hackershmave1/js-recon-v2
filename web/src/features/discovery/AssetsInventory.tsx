import { useEffect, useState } from "react";
import { getAssets } from "../../api/apiClient";
import type { AssetsManifest } from "../../api/types";

export function AssetsInventory({ tenantId, runId }: { tenantId: string; runId: string }) {
  const [manifest, setManifest] = useState<AssetsManifest | null>(null);

  useEffect(() => {
    let live = true;
    getAssets(tenantId, runId).then((m) => { if (live) setManifest(m); }).catch(() => {});
    return () => { live = false; };
  }, [tenantId, runId]);

  if (!manifest) return null;
  return (
    <section className="card">
      <h3>Discovered JavaScript</h3>
      <p className="muted">{manifest.assets.length} asset{manifest.assets.length === 1 ? "" : "s"} · crawl status: {manifest.status}</p>
      <ul>
        {manifest.assets.map((a) => (
          <li key={a.url}>
            <code>{a.url}</code>{" "}
            <span className={`chip chip-${a.fetch_status}`}>fetch: {a.fetch_status}</span>{" "}
            <span className={`chip chip-${a.analyze_status}`}>analyze: {a.analyze_status}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
