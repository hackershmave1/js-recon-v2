const $ = {
  getJSON: (url) => fetch(url).then((r) => r.json()),
  ajax: (opts) => fetch(opts.url, { method: opts.method, body: JSON.stringify(opts.data) }),
};
export function loadSocial() {
  $.getJSON("/api/v1/config");
  $.ajax({ url: "/api/v1/feedback", method: "POST", data: { msg: "hi" } });
}
