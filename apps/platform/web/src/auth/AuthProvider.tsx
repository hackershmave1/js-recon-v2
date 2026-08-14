import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import {
  AUTH_TOKEN_KEY,
  ApiError,
  getMe,
  login as apiLogin,
  setUnauthorizedHandler,
} from "../api/apiClient";

// The login token is the source of the active tenant. We ALSO mirror its tenant id into
// `recon.tenantId` so the existing TenantProvider (and every useTenant() consumer) keeps
// working unchanged — the SPA's tenant plumbing is untouched; only its SOURCE moved from
// a typed-in UUID to a signed login claim.
const TENANT_KEY = "recon.tenantId";
const USER_KEY = "recon.authUser";
const TENANT_NAME_KEY = "recon.authTenantName";

export interface AuthUser {
  username: string;
  role: string;
  tenantId: string;
  tenantName: string | null;
}

interface AuthCtx {
  user: AuthUser | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthCtx | null>(null);

function b64urlDecode(value: string): string {
  const b64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const pad = b64.length % 4 ? "=".repeat(4 - (b64.length % 4)) : "";
  return atob(b64 + pad);
}

/** Decode a session token's payload WITHOUT verifying its signature (the HMAC key is
 * server-side). Used only for UX — which tenant to show, and whether it's already
 * expired — so we never render an authed shell for a token the server will 401. The
 * server is the real authority: a forged/edited token is rejected on the next call. */
function decodeToken(token: string): { tenantId: string; role: string; exp: number } | null {
  try {
    const payload = token.split(".")[0];
    if (!payload) return null;
    const claims = JSON.parse(b64urlDecode(payload));
    if (claims.typ !== "auth") return null;
    if (typeof claims.t !== "string" || typeof claims.exp !== "number") return null;
    return { tenantId: claims.t, role: typeof claims.role === "string" ? claims.role : "", exp: claims.exp };
  } catch {
    return null;
  }
}

function clearAuthStorage(): void {
  [AUTH_TOKEN_KEY, TENANT_KEY, USER_KEY, TENANT_NAME_KEY].forEach((k) => localStorage.removeItem(k));
}

/** Rebuild the logged-in user from a persisted token on cold start — synchronously, so
 * the tenant is available on the first render (no null-tenant window that would break the
 * ?capture= deep-link or the SSE stream; adversarial review Finding 9). An expired or
 * malformed token is cleared and treated as logged out. */
function bootstrapUser(): AuthUser | null {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  if (!token) return null;
  const decoded = decodeToken(token);
  if (!decoded || decoded.exp * 1000 <= Date.now()) {
    clearAuthStorage();
    return null;
  }
  localStorage.setItem(TENANT_KEY, decoded.tenantId); // keep TenantProvider in sync
  return {
    username: localStorage.getItem(USER_KEY) ?? "",
    role: decoded.role,
    tenantId: decoded.tenantId,
    tenantName: localStorage.getItem(TENANT_NAME_KEY),
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => bootstrapUser());

  // Confirm a persisted token with the server once on load (its signature can't be
  // checked in the browser). A 401 => stale/rotated/forged token => log out.
  useEffect(() => {
    if (!localStorage.getItem(AUTH_TOKEN_KEY)) return;
    let cancelled = false;
    getMe()
      .then((me) => {
        if (cancelled) return;
        localStorage.setItem(TENANT_KEY, me.tenant.id);
        if (me.tenant.name) localStorage.setItem(TENANT_NAME_KEY, me.tenant.name);
        setUser((prev) => ({
          username: prev?.username ?? "",
          role: me.role,
          tenantId: me.tenant.id,
          tenantName: me.tenant.name,
        }));
      })
      .catch((err) => {
        if (!cancelled && err instanceof ApiError && err.status === 401) {
          clearAuthStorage();
          setUser(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Any tenant call that 401s means this session is dead (expired/rotated token). Drop to the
  // login screen instead of looping on 401s until a manual reload (review Finding 3).
  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearAuthStorage();
      setUser(null);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = async (username: string, password: string): Promise<void> => {
    const result = await apiLogin(username, password);
    localStorage.setItem(AUTH_TOKEN_KEY, result.token);
    localStorage.setItem(TENANT_KEY, result.tenant.id);
    localStorage.setItem(USER_KEY, result.user);
    if (result.tenant.name) localStorage.setItem(TENANT_NAME_KEY, result.tenant.name);
    setUser({
      username: result.user,
      role: result.role,
      tenantId: result.tenant.id,
      tenantName: result.tenant.name,
    });
  };

  const logout = (): void => {
    clearAuthStorage();
    setUser(null);
  };

  return <Ctx.Provider value={{ user, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used within AuthProvider");
  return v;
}
