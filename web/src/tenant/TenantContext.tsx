import { createContext, useContext, useState, type ReactNode } from "react";

interface TenantCtx { tenantId: string | null; setTenantId: (v: string | null) => void; }
const Ctx = createContext<TenantCtx | null>(null);
const KEY = "recon.tenantId";

export function TenantProvider({ children }: { children: ReactNode }) {
  const [tenantId, setState] = useState<string | null>(() => localStorage.getItem(KEY));
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
