"use client";
// Next.js App Router client chunk. Same-origin relative paths at sinks.
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE; // inlined at build, value not in this chunk

export async function listDocuments(status) {
  const r = await fetch(`/api/documents?status=${status}`, { cache: "no-store" });
  return r.json();
}

export async function patchDocument(id, body) {
  return fetch(`/api/documents/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function graphSummary() {
  return fetch(API_BASE + "/graph/summary", { headers: { authorization: `Bearer ${tok}` } });
}

export function useNav() {
  const router = useRouter();
  return {
    toReview: (queueId) => router.push(`/queue/${queueId}/review`),
    toAdmin: () => router.push("/admin/users"),
    toGdpr: () => router.replace("/legal/gdpr"),
  };
}

export const revalidateTag = () => fetch("/api/revalidate?tag=queue", { method: "POST" });

self.__BUILD_MANIFEST = {
  "/queue/[id]/review": ["static/chunks/pages/queue-4b1.js"],
  "/admin/users": ["static/chunks/pages/admin-users-9c2.js"],
  "/legal/gdpr": ["static/chunks/pages/gdpr-77a.js"],
};

const thumb = `/_next/image?url=${encodeURIComponent(src)}&w=640&q=75`;
