export async function loadProfile() {
  await fetch("/api/v1/profile");
  const userId = window.__uid || "me";
  await fetch(`/api/v1/users/${userId}`);
  const q = "shirt";
  await fetch(`/api/v1/search?q=${q}&limit=20`);
  await fetch("/api/v1/orders", { method: "POST", body: JSON.stringify({ sku: "A1", qty: 2 }) });
}
