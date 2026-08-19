import type { ReactNode } from "react";
import { TenantProvider } from "./tenant/TenantContext";
import { EngagementFilterProvider } from "./features/sessions/EngagementFilterProvider";

// The app-global provider stack that sits below auth and wraps the router: tenant
// identity + the engagement filter (so the sidebar switcher is live on EVERY route
// that renders it, not only /sessions — Starbucks QA #1). Shared by main.tsx (the real
// entry) and the route tests (app.test.tsx) so the two can't drift: removing a provider
// here fails the app-route tests, which is what makes those tests guard production wiring.
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <TenantProvider>
      <EngagementFilterProvider>{children}</EngagementFilterProvider>
    </TenantProvider>
  );
}
