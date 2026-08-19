import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { AppProviders } from "./AppProviders";
import { AuthProvider } from "./auth/AuthProvider";
import { AuthGate } from "./auth/AuthGate";
import { Home, RunWorkspace, OverviewRoute, SourcesRoute, FindingsRoute, ApiSpecRoute, ProbeRoute, TechRoute, HostsRoute } from "./app";
import { SessionsView } from "./features/sessions/SessionsView";
import { installPerfObserver } from "./shell/observability";
// Self-hosted fonts (a recon tool shouldn't phone home to a font CDN). Imported
// before styles.css so the @font-face rules are registered when the design tokens
// (--font-display / --font-sans / --font-mono) reference these families.
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/600.css";
import "@fontsource/space-grotesk/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "./styles.css";

const router = createBrowserRouter([
  { path: "/", Component: Home },
  { path: "/sessions", Component: SessionsView },
  {
    path: "/runs/:id",
    Component: RunWorkspace,
    children: [
      { index: true, Component: OverviewRoute },
      { path: "sources", Component: SourcesRoute },
      { path: "findings", Component: FindingsRoute },
      { path: "api-spec", Component: ApiSpecRoute },
      { path: "probe", Component: ProbeRoute },
      { path: "tech", Component: TechRoute },
      { path: "hosts", Component: HostsRoute },
    ],
  },
]);

// Client-side long-task warnings, tagged by route (D25 observability follow-up).
installPerfObserver();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <AuthGate>
        <AppProviders>
          <RouterProvider router={router} />
        </AppProviders>
      </AuthGate>
    </AuthProvider>
  </StrictMode>,
);
