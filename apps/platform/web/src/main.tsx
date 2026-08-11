import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { TenantProvider } from "./tenant/TenantContext";
import { TenantGate } from "./tenant/TenantGate";
import { Home, RunWorkspace, OverviewRoute, SourcesRoute, FindingsRoute, ApiSpecRoute, ProbeRoute } from "./app";
import { SessionsView } from "./features/sessions/SessionsView";
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
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TenantProvider>
      <TenantGate>
        <RouterProvider router={router} />
      </TenantGate>
    </TenantProvider>
  </StrictMode>,
);
