// Extracted from ProbePanel — the host-selector for resolving relative endpoints.
// The chosen host is sent to the server, which re-serializes curl/http through its
// hardened path (artifacts are never built client-side; ADR-0006).
const CUSTOM = "__custom__";

export function HostSelector({
  hosts, target, useCustom, host, customHost, onPick, onCustom, onCommit,
}: {
  hosts: string[]; target: string | null; useCustom: boolean; host: string;
  customHost: string; onPick: (value: string) => void; onCustom: (value: string) => void;
  onCommit: () => void;
}) {
  return (
    <div className="pb-host-selector">
      <label htmlFor="probe-host" className="muted">Resolve relative paths against</label>
      <select id="probe-host" value={useCustom ? CUSTOM : host} onChange={(e) => onPick(e.target.value)}>
        {hosts.length === 0 && <option value="">{"{{base_url}} (unresolved)"}</option>}
        {hosts.map((h) => (
          <option key={h} value={h}>{h === target ? `${h} (primary, in scope)` : `${h} (in scope)`}</option>
        ))}
        <option value={CUSTOM}>Custom host…</option>
      </select>
      {useCustom && (
        <input
          type="text" aria-label="Custom host" placeholder="api.example.com"
          value={customHost} onChange={(e) => onCustom(e.target.value)}
          onBlur={onCommit}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onCommit(); } }}
        />
      )}
    </div>
  );
}

export const CUSTOM_SENTINEL = CUSTOM;
