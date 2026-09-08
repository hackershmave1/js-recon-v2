// Real absolute URLs at real sinks — but out of scope. Scope, not type, is what demotes these.
Sentry.init({
  dsn: "https://f3a91c8811d44e0e9f3a2c8811d4abcd@o1234567.ingest.sentry.io/4505123456789",
  tracePropagationTargets: ["https://idvs-api.acme.corp/api"],
});

(function (i, s, o, g, r, a, m) { i.GoogleAnalyticsObject = r; })
  (window, document, "script", "https://www.googletagmanager.com/gtag/js?id=G-7XK2Q9",  "gtag");

fetch("https://www.google-analytics.com/g/collect?v=2&tid=G-7XK2Q9", { method: "POST" });

analytics.load("https://cdn.segment.com/analytics.js/v1/9f3a2c8811d4/analytics.min.js");
fetch("https://api.segment.io/v1/track", { method: "POST", body: JSON.stringify(evt) });

const stripe = Stripe("pk_live_51Hx9fA2c8811d4abcd");
fetch("https://api.stripe.com/v1/payment_intents", { method: "POST", headers: { Authorization: "Bearer " + k } });

window.intercomSettings = { app_id: "abcd1234", api_base: "https://api-iam.intercom.io" };
fetch("https://api.launchdarkly.com/sdk/evalx/6540ab/users/" + btoa(JSON.stringify(user)));
