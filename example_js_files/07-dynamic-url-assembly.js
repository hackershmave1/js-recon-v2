// Every flavour of "URL exists but static analysis can't finish the string".
const proto = window.location.protocol;
const host = window.__ENV__.API_HOST;      // runtime injected
const tenant = getTenantFromJwt();

// multi-hole template: shape is known, value is not
const svc = `${proto}//${host}/${tenant}/v1/${resource}`;
fetch(svc);

// URL constructor with dynamic base
const u = new URL(`/v1/${a}/${b}/settings`, window.__ENV__.API_BASE);
fetch(u.toString(), { method: "PUT" });

// lookup table — the paths ARE static, the selection is not
const ENDPOINTS = {
  queue: "/AssignedQueue",
  split: "/DocumentSplitDetailsPagination",
  purge: "/admin/purge-all",
};
fetch(base + ENDPOINTS[key]);

// pure member access — nothing recoverable
axios(cfg.url);
fetch(row.href, { method: row.verb });
fetch(this.props.endpoint);

// method is dynamic too
fetch(`${API}/documents/${id}`, { method: isDraft ? "POST" : "PUT" });

// string built by array join
const path = ["", "api", "v1", "keys"].join("/");
fetch(host + path);
