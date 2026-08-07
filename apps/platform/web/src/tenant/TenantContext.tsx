import { createContext, useContext, useState, type ReactNode } from "react";

interface TenantCtx { tenantId: string | null; setTenantId: (v: string | null) => void; }
const Ctx = createContext<TenantCtx | null>(null);
const KEY = "recon.tenantId";

export function TenantProvider({ children }: { children: ReactNode }) {
  // DEBT D15: cold-start default. An explicit last-used tenant (localStorage) always
  // wins; otherwise fall back to an opt-in build-time VITE_DEFAULT_TENANT_ID so a
  // single-operator/dev deploy need not paste a UUID the operator doesn't know. The
  // default is read (never persisted) so it always tracks the build rather than freezing
  // a stale tenant; TenantGate still validates it before entering. Vite inlines the var
  // into the bundle — set it only for a single-tenant/dev build (see web/.env.example).
  const [tenantId, setState] = useState<string | null>(
    () => localStorage.getItem(KEY) ?? (import.meta.env.VITE_DEFAULT_TENANT_ID || null),
  );
  const setTenantId = (v: string | null) => {
    if (v) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY);
    setState(v);
  };
  return <Ctx.Provider value={{ tenantId, setTenantId }}>{children}</Ctx.Provider>;
}

export function useTenant(): TenantCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTenant must be used within TenantProvider");
  return v;
}
