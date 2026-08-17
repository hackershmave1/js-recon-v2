import { useState, type FormEvent } from "react";
import { ApiError } from "../api/apiClient";
import { useAuth } from "./AuthProvider";
import "./auth.css";

function friendlyError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Invalid username or password.";
    if (err.status === 503) return "Authentication isn't configured on the server.";
    return err.message || "Login failed.";
  }
  return "Couldn't reach the server — is it running?";
}

// A radar-sweep glyph: the "recon" motif (concentric arcs + a sweep line + a
// contact dot), stroked in the lime accent via `currentColor`.
function ReconMark({ size = 30 }: { size?: number }) {
  return (
    <svg className="login-mark" width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9.5" />
      <circle cx="12" cy="12" r="5.5" opacity="0.7" />
      <path d="M12 12 L19 7.5" />
      <circle cx="16" cy="9" r="1.3" fill="currentColor" stroke="none" />
    </svg>
  );
}

const CheckIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
);

const WarnIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 9v4" /><path d="M12 17h.01" />
    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
  </svg>
);

const EyeIcon = ({ off }: { off: boolean }) => (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {off ? (
      <>
        <path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c7 0 10 8 10 8a18 18 0 0 1-2.16 3.19M6.6 6.6A18 18 0 0 0 2 12s3 8 10 8a9 9 0 0 0 5.4-1.6" />
        <path d="M10.6 10.6a2 2 0 0 0 2.8 2.8" /><path d="m2 2 20 20" />
      </>
    ) : (
      <>
        <path d="M2 12s3-8 10-8 10 8 10 8-3 8-10 8-10-8-10-8Z" /><circle cx="12" cy="12" r="3" />
      </>
    )}
  </svg>
);

export function LoginScreen() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
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
    <div className="login">
      <aside className="login-brand">
        <div className="login-brand-inner">
          <div className="login-logo">
            <ReconMark />
            <span className="login-wordmark">Recon Workspace</span>
          </div>
          <h2 className="login-tagline">Reconstruct the API surface hidden in any <b>JavaScript</b> bundle.</h2>
          <ul className="login-points">
            <li><CheckIcon /> Static endpoint, parameter, and secret recon — no traffic against the target.</li>
            <li><CheckIcon /> Export an OpenAPI spec and a recon report from the reconstructed surface.</li>
            <li><CheckIcon /> Capture post-auth runtime JavaScript with the browser extension.</li>
          </ul>
        </div>
        <div className="login-brand-foot">static analysis · SSRF-guarded egress · multi-tenant</div>
      </aside>

      <div className="login-form-col">
        <form className="login-card" onSubmit={onSubmit}>
          <div className="login-form-brand">
            <ReconMark size={24} />
            <span className="login-wordmark">Recon Workspace</span>
          </div>

          <h1 className="login-title">Sign in</h1>
          <p className="login-sub">Access your recon workspace.</p>

          <div className="login-field">
            <label htmlFor="login-username">Username</label>
            <input
              id="login-username"
              name="username"
              value={username}
              autoComplete="username"
              autoFocus
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>

          <div className="login-field">
            <label htmlFor="login-password">Password</label>
            <div className="login-password">
              <input
                id="login-password"
                name="password"
                type={showPassword ? "text" : "password"}
                value={password}
                autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className="login-eye"
                onClick={() => setShowPassword((s) => !s)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                aria-pressed={showPassword}
                title={showPassword ? "Hide password" : "Show password"}
              >
                <EyeIcon off={showPassword} />
              </button>
            </div>
          </div>

          {error && (
            <p className="login-error" role="alert">
              <WarnIcon />
              {error}
            </p>
          )}

          <button type="submit" className="btn-primary login-submit" disabled={!canSubmit}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="login-legal">Authorized use only. Analysis is scoped to in-scope targets.</p>
      </div>
    </div>
  );
}
