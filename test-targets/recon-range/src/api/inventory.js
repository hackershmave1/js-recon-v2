import axios from "axios";
const inventoryApi = axios.create({ baseURL: "/api/v2" });
export async function loadInventory() {
  await inventoryApi.get("/inventory");
  await inventoryApi.post("/checkout", { token: "t" });
  await axios.delete("/api/v1/session");
}
