// Shared config module. Imported by BOTH the entry (main.js) and a lazy chunk
// (orders.js) on purpose: that makes every bundler emit it as a genuine
// cross-chunk / cross-module boundary (a `__webpack_require__(id)` edge, or a
// shared ESM chunk) instead of inlining these consts into one consumer. The
// cross-module case is exactly what the per-file extractor cannot resolve today.
export const API_BASE = "https://api.acme.com";
export const ORDERS_PATH = "/api/v3/orders";
