/**
 * ChatPanel — Natural-language HR intelligence chat interface (F7).
 *
 * Sends questions to POST /query/natural and renders:
 *   - User messages (right-aligned)
 *   - Assistant answers (left-aligned)
 *   - Collapsible tool-call trace per answer
 *   - Loading spinner while the model is thinking
 *
 * Usage:
 *   import { ChatPanel } from "./components/ChatPanel";
 *   <ChatPanel />
 */

import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// ─── Styling constants ────────────────────────────────────────────────────────

const STYLES = {
  panel: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    minHeight: 480,
    maxHeight: 720,
    border: "1px solid var(--primary-10)",
    borderRadius: 10,
    background: "var(--white)",
    overflow: "hidden",
    fontFamily: "inherit",
  },
  header: {
    padding: "12px 16px",
    borderBottom: "1px solid var(--primary-10)",
    background: "var(--light)",
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  headerTitle: {
    fontWeight: 600,
    fontSize: 14,
    color: "var(--dark)",
    flex: 1,
  },
  headerBadge: {
    fontSize: 11,
    color: "var(--mid)",
    background: "var(--primary-10)",
    border: "1px solid var(--primary-10)",
    borderRadius: 4,
    padding: "2px 7px",
  },
  messages: {
    flex: 1,
    overflowY: "auto",
    padding: "12px 16px",
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  inputRow: {
    borderTop: "1px solid var(--primary-10)",
    padding: "10px 12px",
    display: "flex",
    gap: 8,
  },
  input: {
    flex: 1,
    border: "1px solid var(--primary-10)",
    borderRadius: 6,
    padding: "8px 12px",
    fontSize: 14,
    outline: "none",
    resize: "none",
    fontFamily: "inherit",
    lineHeight: 1.4,
  },
  sendBtn: {
    background: "var(--primary)",
    color: "var(--white)",
    border: "none",
    borderRadius: 6,
    padding: "8px 16px",
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
    alignSelf: "flex-end",
  },
  sendBtnDisabled: {
    background: "var(--primary-30)",
    cursor: "not-allowed",
  },
};

// ─── Sub-components ───────────────────────────────────────────────────────────

function UserBubble({ text }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end" }}>
      <div
        style={{
          background: "var(--primary)",
          color: "var(--white)",
          borderRadius: "10px 10px 2px 10px",
          padding: "8px 12px",
          maxWidth: "75%",
          fontSize: 14,
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
        }}
      >
        {text}
      </div>
    </div>
  );
}

function ToolCallRow({ tool }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ fontSize: 11, marginTop: 4 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          background: "none",
          border: "none",
          cursor: "pointer",
          color: "var(--mid)",
          fontSize: 11,
          padding: 0,
          display: "flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        <span>{open ? "▾" : "▸"}</span>
        <span style={{ fontFamily: "monospace" }}>{tool.name}</span>
        <span style={{ color: "var(--mid)", opacity: 0.7 }}>— {tool.result_summary}</span>
      </button>
      {open && (
        <pre
          style={{
            margin: "4px 0 0 16px",
            padding: "6px 8px",
            background: "var(--light)",
            border: "1px solid var(--primary-10)",
            borderRadius: 4,
            fontSize: 11,
            overflowX: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
          {JSON.stringify(tool.input, null, 2)}
        </pre>
      )}
    </div>
  );
}

function AssistantBubble({ message }) {
  const { answer, tools_used = [], latency_ms, turns } = message;
  return (
    <div style={{ display: "flex", justifyContent: "flex-start" }}>
      <div style={{ maxWidth: "85%" }}>
        <div
          style={{
            background: "var(--primary-10)",
            color: "var(--dark)",
            borderRadius: "10px 10px 10px 2px",
            padding: "8px 12px",
            fontSize: 14,
            lineHeight: 1.6,
            whiteSpace: "pre-wrap",
          }}
        >
          {answer}
        </div>
        {tools_used.length > 0 && (
          <div style={{ marginTop: 4, paddingLeft: 4 }}>
            {tools_used.map((t, i) => (
              <ToolCallRow key={i} tool={t} />
            ))}
          </div>
        )}
        <div style={{ fontSize: 10, color: "var(--mid)", marginTop: 3, paddingLeft: 2 }}>
          {latency_ms}ms · {turns} turn{turns !== 1 ? "s" : ""}
        </div>
      </div>
    </div>
  );
}

function ThinkingBubble() {
  return (
    <div style={{ display: "flex", justifyContent: "flex-start" }}>
      <div
        style={{
          background: "var(--primary-10)",
          borderRadius: "10px 10px 10px 2px",
          padding: "10px 14px",
          fontSize: 20,
          letterSpacing: 4,
          color: "var(--mid)",
        }}
      >
        ···
      </div>
    </div>
  );
}

function ErrorBubble({ text }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-start" }}>
      <div
        style={{
          background: "#FDEAEA",
          border: "1px solid #E03448",
          color: "#7A1020",
          borderRadius: "10px 10px 10px 2px",
          padding: "8px 12px",
          fontSize: 13,
          maxWidth: "85%",
        }}
      >
        {text}
      </div>
    </div>
  );
}

// ─── Suggested questions ──────────────────────────────────────────────────────

const SUGGESTIONS = [
  "Who are the top 5 single points of failure right now?",
  "Are there any active communication silos?",
  "What happens to the network if Alice leaves?",
  "Who has the highest knowledge concentration risk?",
];

// ─── Main component ───────────────────────────────────────────────────────────

export function ChatPanel() {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);
  const textareaRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const send = useCallback(async (question) => {
    const q = question.trim();
    if (!q || loading) return;
    setDraft("");
    setMessages((prev) => [...prev, { role: "user", text: q }]);
    setLoading(true);

    try {
      const resp = await fetch(`${API_BASE}/query/natural`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail ?? `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setMessages((prev) => [...prev, { role: "assistant", ...data }]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: "error", text: `Request failed: ${e.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  }, [loading]);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(draft);
    }
  };

  const isEmpty = messages.length === 0 && !loading;

  return (
    <div style={STYLES.panel}>
      {/* Header */}
      <div style={STYLES.header}>
        <span style={STYLES.headerTitle}>Ask the Org</span>
        <span style={STYLES.headerBadge}>AI · Claude</span>
      </div>

      {/* Messages */}
      <div style={STYLES.messages}>
        {isEmpty && (
          <div style={{ textAlign: "center", color: "var(--mid)", paddingTop: 32 }}>
            <p style={{ fontSize: 13, marginBottom: 16 }}>
              Ask anything about your organisation's collaboration health.
            </p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, justifyContent: "center" }}>
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  style={{
                    background: "var(--white)",
                    border: "1px solid var(--primary-10)",
                    borderRadius: 6,
                    padding: "5px 10px",
                    fontSize: 12,
                    color: "var(--dark)",
                    cursor: "pointer",
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => {
          if (msg.role === "user") return <UserBubble key={i} text={msg.text} />;
          if (msg.role === "assistant") return <AssistantBubble key={i} message={msg} />;
          if (msg.role === "error") return <ErrorBubble key={i} text={msg.text} />;
          return null;
        })}

        {loading && <ThinkingBubble />}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div style={STYLES.inputRow}>
        <textarea
          ref={textareaRef}
          rows={2}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a question… (Enter to send, Shift+Enter for newline)"
          disabled={loading}
          style={{
            ...STYLES.input,
            background: loading ? "var(--light)" : "var(--white)",
          }}
        />
        <button
          onClick={() => send(draft)}
          disabled={loading || !draft.trim()}
          style={{
            ...STYLES.sendBtn,
            ...(loading || !draft.trim() ? STYLES.sendBtnDisabled : {}),
          }}
        >
          Send
        </button>
      </div>
    </div>
  );
}
