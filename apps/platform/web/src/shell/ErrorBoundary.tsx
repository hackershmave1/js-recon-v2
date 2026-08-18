import { Component, type ErrorInfo, type ReactNode } from "react";

// Contain a render crash to one page instead of blanking the whole workspace, and
// log it with the route + component stack so a UI failure is diagnosable (D25
// follow-up: the Sources view could previously hang or blank with nothing in the
// logs). Reset by navigating away (the caller keys this by route) or "Try again".
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`[ui] render error at ${location.pathname}:`, error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="card" role="alert">
          <h2 className="rp-title">Something went wrong on this page</h2>
          <p className="muted">{this.state.error.message}</p>
          <button type="button" onClick={() => this.setState({ error: null })}>Try again</button>
        </div>
      );
    }
    return this.props.children;
  }
}
