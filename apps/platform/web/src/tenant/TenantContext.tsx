import { createContext, useContext, useState, type ReactNode } from "react";

interface TenantCtx { tenantId: string | null; setTenantId: (v: string | null) => void; }
const Ctx = createContext<TenantCtx | null>(null);
const KEY = "recon.tenantId";

export function TenantProvider({ children }: { children: ReactNode }) {
  // The active tenant now comes from the login token: AuthProvider mirrors the signed
  // tenant claim into `recon.tenantId` before this provider mounts. This seed order is
  // just the fallback for reading that value — a persisted last-used tenant (localStorage)
  // wins, else an opt-in build-time VITE_DEFAULT_TENANT_ID (DEBT D15, read but never
  // persisted so it tracks the build). Post-login the localStorage value is always set,
  // so the env default is now effectively vestigial — kept as a harmless cold-start net.
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
