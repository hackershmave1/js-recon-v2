import { useState, type ReactNode } from "react";
import { useTenant } from "./TenantContext";

// No stricter than the server's Python uuid.UUID(): drop urn:/uuid: prefixes
// (independent, global), strip only leading/trailing braces, drop hyphens,
// then require 32 hex chars.
export function isValidTenant(v: string): boolean {
  let hex = v.trim().toLowerCase().replace(/urn:/g, "").replace(/uuid:/g, "");
  hex = hex.replace(/^[{}]+|[{}]+$/g, "").replace(/-/g, "");
  return /^[0-9a-f]{32}$/.test(hex);
}

export function TenantGate({ children }: { children: ReactNode }) {
  const { tenantId, setTenantId } = useTenant();
  const [draft, setDraft] = useState("");
  if (tenantId && isValidTenant(tenantId)) return <>{children}</>;
  const valid = isValidTenant(draft);
  return (
    <form className="card" onSubmit={(e) => { e.preventDefault(); if (valid) setTenantId(draft.trim()); }}>
      <label htmlFor="tenant">Tenant ID (UUID)</label>
      <input id="tenant" value={draft} onChange={(e) => setDraft(e.target.value)} autoComplete="off" />
      {draft && !valid && <p className="sev-high">Must be a UUID.</p>}
      <button type="submit" disabled={!valid}>Continue</button>
    </form>
  );
}
