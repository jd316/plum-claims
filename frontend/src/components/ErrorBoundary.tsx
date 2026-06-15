import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * App-level error boundary. Without it, any render-time exception in the tree
 * unmounts the whole React app and the user sees a blank white page. This catches
 * the error, logs it for diagnostics, and shows a recoverable fallback with a
 * reload action — so a single bad render degrades gracefully instead of bricking
 * the UI. (React error boundaries must be class components.)
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface to the console for diagnostics; a real deployment would forward this
    // to an error-tracking sink (e.g. Sentry) here.
    console.error("Unhandled UI error:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div
          role="alert"
          className="flex min-h-screen flex-col items-center justify-center gap-4 bg-cream px-6 text-center dark:bg-plum-900"
        >
          <h1 className="font-serif text-2xl text-plum-800 dark:text-creamtext">
            Something went wrong
          </h1>
          <p className="max-w-md text-sm text-plum-800/70 dark:text-creamtext/70">
            The page hit an unexpected error and could not render. Your data is safe —
            reloading usually fixes it.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-xl bg-plum-800 px-5 py-2.5 text-sm font-medium text-cream transition-opacity hover:opacity-90 dark:bg-creamtext dark:text-plum-900"
          >
            Reload page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
