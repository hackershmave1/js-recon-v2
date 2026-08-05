export function loadSecure(token, sig, ts) {
  return fetch("/api/v1/secure", {
    headers: { Authorization: `Bearer ${token}`, "X-Signature": sig, "X-Timestamp": ts },
  });
}
