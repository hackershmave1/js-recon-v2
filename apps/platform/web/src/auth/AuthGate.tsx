import type { ReactNode } from "react";
import { useAuth } from "./AuthProvider";
import { HandoffConfirm } from "./HandoffConfirm";
import { LoginScreen } from "./LoginScreen";

/** Blocks the app until a user is logged in. Lives in its own file (not AuthProvider)
 * so AuthProvider need not import LoginScreen — keeping the provider free of a UI cycle.
 * An extension handoff (ADR-0011) comes first: neutral while the code is exchanged (no login
 * flash), then a one-click confirmation of whose workspace this is. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { user, handoff } = useAuth();
  if (handoff.status === "pending") return <div className="login" aria-busy="true" />;
  if (handoff.status === "offer") return <HandoffConfirm />;
  if (!user) return <LoginScreen />;
  return <>{children}</>;
}
