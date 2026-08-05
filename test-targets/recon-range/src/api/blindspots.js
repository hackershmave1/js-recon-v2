function pickUrl() { return "/api/v1/dynamic"; }
function makeClient() { return { get: (p) => fetch(p) }; }
export function blindSpots() {
  new EventSource("/api/v1/stream");
  const resource = "widgets";
  fetch("/api/v1/" + resource);
  const u = pickUrl();
  fetch(u);
  makeClient().get("/api/v1/hidden");
}
