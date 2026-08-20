import { API_BASE, ORDERS_PATH } from "./base.js";

// The request URL is assembled entirely from consts that live in ANOTHER module
// (base.js). Today's AST-local extractor sees `fetch(API_BASE + ORDERS_PATH)`,
// can't resolve either identifier (both are cross-module imports), and — by its
// honesty rule — counts the call as UNATTRIBUTED rather than guessing.
//
// The cross-module resolver (P2) should walk the module graph and reconstruct:
//     GET https://api.acme.com/api/v3/orders
export async function loadOrders() {
  await fetch(API_BASE + ORDERS_PATH);
}
