import { Component } from "react";

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div
        style={{
          minHeight: "100vh",
          background: "var(--light)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "48px",
        }}
      >
        <div
          style={{
            background: "var(--white)",
            borderRadius: "12px",
            boxShadow: "0 1px 4px rgba(0,0,0,.08)",
            overflow: "hidden",
            maxWidth: "560px",
            width: "100%",
          }}
        >
          <div style={{ height: "3px", background: "#E03448" }} />
          <div style={{ padding: "40px" }}>
            <div
              style={{
                fontFamily: "var(--fb)",
                fontSize: "9px",
                letterSpacing: "4px",
                textTransform: "uppercase",
                color: "#E03448",
                marginBottom: "16px",
                display: "flex",
                alignItems: "center",
                gap: "12px",
              }}
            >
              <span
                style={{
                  display: "block",
                  width: "24px",
                  height: "1px",
                  background: "#E03448",
                }}
              />
              Application error
            </div>

            <h1
              style={{
                fontFamily: "var(--fd)",
                fontSize: "28px",
                fontWeight: 300,
                color: "var(--dark)",
                margin: "0 0 12px",
                lineHeight: 1.2,
              }}
            >
              Something went{" "}
              <em style={{ fontStyle: "italic", color: "#E03448" }}>wrong</em>
            </h1>

            <p
              style={{
                fontFamily: "var(--fb)",
                fontSize: "14px",
                color: "var(--mid)",
                margin: "0 0 24px",
                lineHeight: 1.7,
              }}
            >
              The dashboard encountered an unexpected error. Check that the API
              is running on port 8000 and refresh the page.
            </p>

            {this.state.error && (
              <pre
                style={{
                  fontFamily: "Courier New, monospace",
                  fontSize: "12px",
                  background: "var(--light)",
                  borderRadius: "6px",
                  padding: "12px 16px",
                  color: "var(--dark)",
                  overflow: "auto",
                  marginBottom: "24px",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {this.state.error.message}
              </pre>
            )}

            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              style={{
                background: "var(--primary)",
                color: "var(--white)",
                border: "none",
                borderRadius: "8px",
                padding: "10px 20px",
                fontFamily: "var(--fb)",
                fontSize: "13px",
                fontWeight: 600,
                cursor: "pointer",
                letterSpacing: "0.5px",
              }}
            >
              Retry →
            </button>
          </div>
        </div>
      </div>
    );
  }
}
