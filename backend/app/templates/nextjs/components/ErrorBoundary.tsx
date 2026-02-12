"use client";
import React from "react";

interface Props {
  children: React.ReactNode;
  name?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          padding: "24px",
          margin: "8px 0",
          background: "#1a1a1a",
          border: "1px solid #333",
          borderRadius: "8px",
          color: "#888",
          fontSize: "14px",
          fontFamily: "system-ui, sans-serif",
        }}>
          <span style={{ color: "#666" }}>
            {this.props.name || "Section"} failed to render
          </span>
        </div>
      );
    }
    return this.props.children;
  }
}
