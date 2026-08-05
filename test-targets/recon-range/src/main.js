import { loadProfile } from "./api/profile.js";
import { updateCart } from "./api/cart.js";
import { pingThirdParties } from "./api/thirdparty.js";
import { loadSecure } from "./api/secure.js";
import { blindSpots } from "./api/blindspots.js";
import { KEYS } from "./secrets.js";

window.__reconKeys = KEYS;
loadProfile();
updateCart();
pingThirdParties();
loadSecure("tok", "sig", "123");
blindSpots();

const lazy = [
  { name: "inventory", load: () => import("./api/inventory.js") },
  { name: "social", load: () => import("./api/social.js") },
  { name: "live", load: () => import("./api/live.js") },
];
function whenVisible(el, cb) {
  new IntersectionObserver((entries, obs) => {
    if (entries.some(e => e.isIntersecting)) { obs.disconnect(); cb(); }
  }).observe(el);
}
for (const { name, load } of lazy) {
  const s = document.createElement("div");
  s.style.height = "1200px";
  s.textContent = name;
  document.body.appendChild(s);
  whenVisible(s, () => load().then(m => { const fn = Object.values(m).find(v => typeof v === "function"); if (fn) fn(); }));
}
