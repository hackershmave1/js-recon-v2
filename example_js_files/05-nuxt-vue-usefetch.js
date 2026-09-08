// Nuxt 3 / Vue. baseURL is passed as a *sibling option*, not concatenated into the path.
import { useRuntimeConfig, useFetch, $fetch } from "#imports";

export function useDocumentContent(id) {
  const config = useRuntimeConfig();
  return useFetch(`/DocumentContent`, {
    baseURL: config.public.apiBase, // "https://idvs.acme.dev/idvs/v1.0" at runtime
    query: { documentId: id },
  });
}

export async function splitDetails(payload) {
  const config = useRuntimeConfig();
  return $fetch("/DocumentSplitDetailsPagination", {
    baseURL: config.public.apiBase,
    method: "POST",
    body: payload,
  });
}

// Nuxt server route — same-origin, no baseURL
export const translations = () => useFetch("/api/_nuxt/translations?locale=he-IL");

// hard-coded absolute, bypasses runtime config
export const legacyPing = () => $fetch("https://idvs-legacy.acme.dev/api/ping");

export default defineNuxtRouteMiddleware((to) => {
  if (to.path.startsWith("/admin")) return navigateTo("/auth/login?next=" + to.path);
});
