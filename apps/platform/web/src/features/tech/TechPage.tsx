import type { TechnologiesResponse } from "../../api/types";
import "./tech.css";

// The per-host technology stack — the threat-model-grade surface (name · category ·
// version · confidence · evidence). Version is "—" when not statically derivable
// (Phase 1 honesty — T12).
// runFinished=false: the run is still active, so detection (which runs at the end of analysis)
// simply hasn't happened yet.
export function TechPage({ data, runFinished = true }: { data: TechnologiesResponse; runFinished?: boolean }) {
  const hosts = Object.entries(data.hosts);
  if (data.detection_ran === false) {
    return (
      <div className="card">
        <h2 className="rp-title">Tech stack</h2>
        <p className="muted">
          {runFinished
            ? 'Not collected for this run. Technology detection reads the response headers and page signals a crawl collects; extension captures, uploads and single-URL runs don\'t provide them, so the stack is unknown — not "no technologies".'
            : "Technology detection runs at the end of analysis — check back when this run finishes."}
        </p>
      </div>
    );
  }
  if (data.count === 0 || hosts.length === 0) {
    return (
      <div className="card">
        <h2 className="rp-title">Tech stack</h2>
        <p className="muted">No technologies detected for this run.</p>
      </div>
    );
  }
  return (
    <div className="card">
      <h2 className="rp-title">Tech stack</h2>
      {hosts.map(([host, techs]) => (
        <section key={host} className="tech-host">
          <h3 className="tech-host-name">{host}</h3>
          <table className="tech-table">
            <thead>
              <tr><th>Technology</th><th>Category</th><th>Version</th><th>Confidence</th><th>Evidence</th></tr>
            </thead>
            <tbody>
              {techs.map((t) => (
                <tr key={t.name}>
                  <td>{t.name}</td>
                  <td>{t.categories.join(", ") || "—"}</td>
                  <td>{t.version ?? "—"}</td>
                  <td>{t.confidence}</td>
                  <td className="tech-evidence">{t.evidence.join("; ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
