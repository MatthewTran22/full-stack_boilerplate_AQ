"use client";

export default function GlobalError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <html>
      <body style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh", margin: 0, fontFamily: "system-ui", background: "#0a0a0a", color: "#fff" }}>
        <div style={{ textAlign: "center" }}>
          <h2>Something went wrong</h2>
          <button onClick={reset} style={{ marginTop: 16, padding: "8px 16px", background: "#fff", color: "#000", border: "none", borderRadius: 6, cursor: "pointer" }}>
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
