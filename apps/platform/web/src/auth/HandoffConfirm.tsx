import { useAuth } from "./AuthProvider";
import "./auth.css";

/** ADR-0011 (review C2): the extension's handoff never signs in silently. A crafted link can
 * carry a code for SOMEONE ELSE's account, so the researcher sees whose workspace this is and
 * confirms with one click. The new session is held in memory until then. */
export function HandoffConfirm() {
  const { handoff, user } = useAuth();
  const offer = handoff.offer;
  if (!offer) return null;
  const current = user && user.username && user.username !== offer.user ? user.username : null;
  return (
    <div className="login">
      <div className="login-form-col" style={{ gridColumn: "1 / -1" }}>
        <div className="login-card" role="group" aria-labelledby="handoff-title">
          <h1 className="login-title" id="handoff-title">Open in workspace</h1>
          <p className="login-sub">
            You opened this from the capture extension.
            {current ? ` You are currently signed in as ${current}.` : ""}
          </p>
          <button type="button" className="login-submit" onClick={handoff.accept} autoFocus>
            Continue as {offer.user} · {offer.tenant.name ?? "workspace"}
          </button>
          <p className="login-legal" style={{ marginTop: 14 }}>
            Not you?{" "}
            <button type="button" className="link" onClick={handoff.decline}>
              Sign in with a password instead
            </button>
          </p>
        </div>
      </div>
    </div>
  );
}
