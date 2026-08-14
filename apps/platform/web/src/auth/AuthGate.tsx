import type { ReactNode } from "react";
import { useAuth } from "./AuthProvider";
import { LoginScreen } from "./LoginScreen";

/** Blocks the app until a user is logged in. Lives in its own file (not AuthProvider)
 * so AuthProvider need not import LoginScreen — keeping the provider free of a UI cycle. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (!user) return <LoginScreen />;
  return <>{children}</>;
}
