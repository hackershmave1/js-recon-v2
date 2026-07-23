import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { TenantProvider } from "./tenant/TenantContext";
import { TenantGate } from "./tenant/TenantGate";
import { Home, RunWorkspace } from "./app";
import "./styles.css";

const router = createBrowserRouter([
  { path: "/", Component: Home },
  { path: "/runs/:id", Component: RunWorkspace },
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
