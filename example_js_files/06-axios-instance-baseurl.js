// The single most common real-world shape: baseURL set once, every call site relative.
import axios from "axios";

const api = axios.create({
  baseURL: "https://apigatewayazeu-dev.accenture.com/idvs/mfe/dev/v1.0",
  withCredentials: true,
  headers: { "x-tenant": "idvs-dev" },
});

const admin = axios.create({ baseURL: window.__CFG__.adminApi }); // base unknown statically

export const getQueue = (p) => api.get("/AssignedQueue", { params: p });
export const assignDoc = (d) => api.post("/AssignedDocument", d);
export const authConfig = () => api.get("/Authentication");
export const clientAuth = (n) => api.get(`/Authentication/Client?ClientName=${n}`);
export const history = (id) => api.get("/DocumentHistory", { params: { id } });

export const deleteUser = (id) => admin.delete(`/users/${id}`);
export const impersonate = (id) => admin.post(`/users/${id}/impersonate`);

// interceptor leaks a second host
api.interceptors.response.use(null, (e) => {
  if (e.response?.status === 401) {
    return axios.post("https://auth.accenture.com/oauth2/token/refresh", {}, { withCredentials: true });
  }
  throw e;
});
