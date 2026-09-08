// Non-fetch/axios sinks. If the detector only knows fetch+axios it misses all of these.
export const gql = (query, variables) =>
  fetch("https://gql.acme.io/graphql", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });

export const Q_QUEUE = `query GetQueue($id: ID!) { queue(id:$id){ id documents { id status } } }`;
export const M_PURGE = `mutation PurgeQueue($id: ID!) { purgeQueue(id:$id){ ok } }`;

export const socket = (t) => new WebSocket(`wss://rt.acme.io/socket?token=${t}`);
export const legacySocket = new WebSocket("ws://rt-legacy.acme.io:8080/stream");
export const stream = new EventSource("/api/events/stream");

export function report(payload) {
  navigator.sendBeacon("/api/telemetry/client-error", JSON.stringify(payload));
}

const xhr = new XMLHttpRequest();
xhr.open("DELETE", "https://idvs-api.acme.corp/api/Document/" + docId, true);
xhr.send();

importScripts("https://cdn.acme.io/worker/v3/ocr-parser.js");
const w = new Worker(new URL("./heavy.worker.js", import.meta.url));

document.querySelector("form#upload").action = "https://idvs-api.acme.corp/api/Document/Upload";
