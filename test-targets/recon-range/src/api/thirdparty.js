export function pingThirdParties() {
  fetch("https://www.google-analytics.com/g/collect?v=2&tid=G-XXXX");
  fetch("https://api.stripe.com/v1/tokens", { method: "POST" });
  fetch("https://o0.ingest.sentry.io/api/1/envelope/", { method: "POST" });
}
