// react-router route table + navigation/auth targets. Not API calls, but real attack surface.
const routes = [
  { path: "/", element: "<Home/>" },
  { path: "/queue/:queueId/review", element: "<Review/>" },
  { path: "/admin/users", element: "<AdminUsers/>", loader: () => fetch("/api/admin/users") },
  { path: "/admin/feature-flags", element: "<Flags/>" },
  { path: "/internal/debug-console", element: "<Debug/>" },
  { path: "/legal/gdpr", element: "<Gdpr/>" },
];

const AUTHORITY = "https://login.microsoftonline.com/72f988bf-86f1-41af-91ab-2d7cd011db47";

export function login() {
  window.location.href =
    `${AUTHORITY}/oauth2/v2.0/authorize?client_id=a1b2c3d4-1111-2222-3333-444455556666` +
    `&response_type=code&redirect_uri=${encodeURIComponent("https://idvs.acme.corp/auth/callback")}` +
    `&scope=${encodeURIComponent("openid profile api://idvs-api/.default")}&state=${nonce()}`;
}

export const OIDC_DISCOVERY = AUTHORITY + "/v2.0/.well-known/openid-configuration";
export const LOGOUT = `${AUTHORITY}/oauth2/v2.0/logout?post_logout_redirect_uri=https://idvs.acme.corp/`;

export function openDocs() {
  window.open("https://idvs-api.acme.corp/swagger/index.html", "_blank");
}
export const SPEC = "https://idvs-api.acme.corp/swagger/v1/swagger.json";
