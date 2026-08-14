import { useState, type FormEvent } from "react";
import { ApiError } from "../api/apiClient";
import { useAuth } from "./AuthProvider";

function friendlyError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Invalid username or password.";
    if (err.status === 503) return "Authentication isn't configured on the server.";
    return err.message || "Login failed.";
  }
  return "Couldn't reach the server — is it running?";
}

export function LoginScreen() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      // On success the provider flips `user`, and AuthGate swaps in the app.
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setBusy(false);
    }
  };

  const canSubmit = username.trim().length > 0 && password.length > 0 && !busy;

  return (
    <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: "24px" }}>
      <form className="card" onSubmit={onSubmit} style={{ width: "min(360px, 100%)" }}>
        <h1 style={{ marginTop: 0 }}>Sign in</h1>
        <p style={{ marginTop: 0, opacity: 0.7 }}>Recon Workspace</p>

        <label htmlFor="login-username">Username</label>
        <input
          id="login-username"
          name="username"
          value={username}
          autoComplete="username"
          autoFocus
          onChange={(e) => setUsername(e.target.value)}
        />

        <label htmlFor="login-password">Password</label>
        <input
          id="login-password"
          name="password"
          type="password"
          value={password}
          autoComplete="current-password"
          onChange={(e) => setPassword(e.target.value)}
        />

        {error && (
          <p className="sev-high" role="alert">
            {error}
          </p>
        )}

        <button type="submit" className="shell-btn shell-btn-primary" disabled={!canSubmit}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
