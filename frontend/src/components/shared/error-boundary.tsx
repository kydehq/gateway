import { Component, type ErrorInfo, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  children: ReactNode;
  /** Key used to force a reset — changing it remounts the boundary. */
  resetKey?: string;
}

interface State {
  error: Error | null;
}

// Plain class component — this is the React 19 idiomatic shape for
// error boundaries; there's no hook equivalent yet.
class BoundaryImpl extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface to console; a real app would send to an error tracker.
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  reset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex flex-col items-start gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-6">
        <div className="flex items-center gap-2 text-destructive">
          <AlertTriangle className="h-5 w-5" />
          <h2 className="text-base font-semibold">Something broke on this page.</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          {this.state.error.message || "An unexpected error occurred."}
        </p>
        {import.meta.env.DEV && this.state.error.stack ? (
          <pre className="max-h-40 w-full overflow-auto rounded-sm bg-muted/40 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
            {this.state.error.stack}
          </pre>
        ) : null}
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={this.reset}>
            <RefreshCw className="mr-2 h-3.5 w-3.5" /> Try again
          </Button>
          <Button variant="ghost" size="sm" onClick={() => window.location.reload()}>
            Reload page
          </Button>
        </div>
      </div>
    );
  }
}

// Wrapper that resets automatically on route change — otherwise a crash
// on /timeline would stay on screen when the user navigates to /overview.
export function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const location = useLocation();
  return <BoundaryImpl resetKey={location.pathname}>{children}</BoundaryImpl>;
}

export { BoundaryImpl as ErrorBoundary };
