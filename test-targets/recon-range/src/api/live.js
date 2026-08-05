export function openLive() {
  return new WebSocket("wss://api.recon-range.test/ws/live");
}
