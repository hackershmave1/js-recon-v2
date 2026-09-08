// Angular production main.js (partially minified). Two hard cases:
//   1) base URL lives in an `environment` object
//   2) an HttpInterceptor rewrites relative URLs, so call sites look host-less
const environment = {
  production: true,
  apiBaseUrl: "https://idvs-api.acme.corp/api",
  reportingUrl: "https://reporting.acme.corp/rpt/v3",
  authority: "https://login.microsoftonline.com/72f988bf-86f1-41af-91ab-2d7cd011db47/v2.0",
  clientId: "a1b2c3d4-1111-2222-3333-444455556666",
};

class DocumentService {
  constructor(http) { this.http = http; }
  getTab(id) { return this.http.get(`${environment.apiBaseUrl}/DocumentTab?DocumentId=${id}`); }
  postFieldHistory(b) { return this.http.post(environment.apiBaseUrl + "/FieldHistory", b); }
  // relative — resolved only by the interceptor below
  getSiteLinks() { return this.http.get("/SiteLinks?active=true"); }
  deleteDoc(id) { return this.http.delete("/Document/" + id); }
  exportReport(q) { return this.http.get(environment.reportingUrl + "/export", { params: q }); }
}

class ApiPrefixInterceptor {
  intercept(req, next) {
    const url = req.url.startsWith("http") ? req.url : `${environment.apiBaseUrl}${req.url}`;
    return next.handle(req.clone({ url, withCredentials: true }));
  }
}
