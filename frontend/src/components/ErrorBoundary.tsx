import { Component, type ReactNode } from "react";

interface Props { children: ReactNode; label?: string }
interface State { error: Error | null }

/** Catches render errors anywhere in the subtree so a single bad panel can
 *  never blank the whole dashboard (React unmounts everything without this). */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("ErrorBoundary caught:", error);
  }

  render() {
    if (this.state.error) {
      const label = this.props.label ?? "This section";
      return (
        <div className="card" style={{ borderColor: "#f8717155" }}>
          <b>{label}</b> failed to render.
          <p className="small muted">{String(this.state.error?.message || this.state.error)}</p>
          <button
            className="ghost small"
            onClick={() => {
              try { location.reload(); } catch { /* noop */ }
            }}
          >
            reload dashboard
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
