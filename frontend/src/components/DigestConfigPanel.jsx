import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

const API_BASE = "/api";

async function fetchDigestConfig() {
  const res = await fetch(`${API_BASE}/admin/digest-config`, {
    headers: { "X-Admin-Key": localStorage.getItem("admin_key") || "" },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function saveDigestConfig(body) {
  const res = await fetch(`${API_BASE}/admin/digest-config`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Key": localStorage.getItem("admin_key") || "",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

function Toggle({ checked, onChange, label }) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: "10px", cursor: "pointer" }}>
      <div
        onClick={() => onChange(!checked)}
        style={{
          width: "40px", height: "22px", borderRadius: "11px",
          background: checked ? "#27B97C" : "#D1D5DB",
          position: "relative", transition: "background 200ms", cursor: "pointer",
        }}
      >
        <div style={{
          width: "16px", height: "16px", borderRadius: "50%", background: "#fff",
          position: "absolute", top: "3px",
          left: checked ? "21px" : "3px",
          transition: "left 200ms",
          boxShadow: "0 1px 3px rgba(0,0,0,.2)",
        }} />
      </div>
      <span style={{ fontFamily: "var(--fb)", fontSize: "14px", color: "var(--dark)" }}>{label}</span>
    </label>
  );
}

export default function DigestConfigPanel() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["digestConfig"], queryFn: fetchDigestConfig });

  const [emails, setEmails]     = useState([]);
  const [emailInput, setEmailInput] = useState("");
  const [webhook, setWebhook]   = useState("");
  const [enableEmail, setEnableEmail] = useState(false);
  const [enableSlack, setEnableSlack] = useState(false);
  const [tz, setTz]             = useState("UTC");
  const [toast, setToast]       = useState(null);
  const [initialised, setInit]  = useState(false);

  if (data && !initialised) {
    setEmails(data.email_recipients || []);
    setEnableEmail(data.enabled_email);
    setEnableSlack(data.enabled_slack);
    setTz(data.timezone || "UTC");
    setInit(true);
  }

  const mutation = useMutation({
    mutationFn: saveDigestConfig,
    onSuccess: () => {
      queryClient.invalidateQueries(["digestConfig"]);
      setToast({ type: "success", text: "Digest config saved." });
      setTimeout(() => setToast(null), 3000);
    },
    onError: (e) => {
      setToast({ type: "error", text: e.message });
      setTimeout(() => setToast(null), 4000);
    },
  });

  function addEmail(e) {
    e.preventDefault();
    const v = emailInput.trim();
    if (v && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v) && !emails.includes(v)) {
      setEmails(prev => [...prev, v]);
      setEmailInput("");
    }
  }

  function removeEmail(email) {
    setEmails(prev => prev.filter(e => e !== email));
  }

  function handleSave() {
    mutation.mutate({
      email_recipients: emails,
      slack_webhook_url: webhook || null,
      enabled_email: enableEmail,
      enabled_slack: enableSlack,
      timezone: tz,
    });
  }

  if (isLoading) return (
    <div style={{ padding: "24px", fontFamily: "var(--fb)", color: "var(--mid)" }}>Loading digest config…</div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
      {/* Toast */}
      {toast && (
        <div style={{
          padding: "12px 16px", borderRadius: "8px",
          background: toast.type === "success" ? "#E0F7EF" : "#FDEAEA",
          color: toast.type === "success" ? "#0D5C3A" : "#7A1020",
          fontFamily: "var(--fb)", fontSize: "14px",
        }}>
          {toast.text}
        </div>
      )}

      {/* Email section */}
      <div>
        <div style={{ fontFamily: "var(--fb)", fontSize: "12px", fontWeight: 600, letterSpacing: "2px", textTransform: "uppercase", color: "var(--mid)", marginBottom: "10px" }}>
          Email Recipients
        </div>
        <form onSubmit={addEmail} style={{ display: "flex", gap: "8px", marginBottom: "10px" }}>
          <input
            value={emailInput}
            onChange={e => setEmailInput(e.target.value)}
            placeholder="name@company.com"
            type="email"
            style={{
              flex: 1, border: "1px solid var(--primary-30)", borderRadius: "8px",
              padding: "9px 14px", fontFamily: "var(--fb)", fontSize: "14px", outline: "none",
            }}
          />
          <button type="submit" style={{
            background: "var(--primary)", color: "#fff", border: "none",
            borderRadius: "8px", padding: "9px 18px", cursor: "pointer", fontFamily: "var(--fb)", fontSize: "14px",
          }}>
            Add
          </button>
        </form>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
          {emails.map(email => (
            <span key={email} style={{
              background: "var(--primary-10)", borderRadius: "20px", padding: "4px 12px",
              fontFamily: "var(--fb)", fontSize: "13px", color: "var(--primary)",
              display: "flex", alignItems: "center", gap: "6px",
            }}>
              {email}
              <button
                onClick={() => removeEmail(email)}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--mid)", fontSize: "14px", lineHeight: 1 }}
              >×</button>
            </span>
          ))}
          {emails.length === 0 && <span style={{ fontFamily: "var(--fb)", fontSize: "13px", color: "var(--mid)" }}>No recipients added.</span>}
        </div>
      </div>

      {/* Slack webhook */}
      <div>
        <div style={{ fontFamily: "var(--fb)", fontSize: "12px", fontWeight: 600, letterSpacing: "2px", textTransform: "uppercase", color: "var(--mid)", marginBottom: "10px" }}>
          Slack Webhook URL {data?.slack_webhook_url_set && <span style={{ color: "#27B97C" }}>✓ set</span>}
        </div>
        <input
          value={webhook}
          onChange={e => setWebhook(e.target.value)}
          type="password"
          placeholder={data?.slack_webhook_url_set ? "••••••••• (leave blank to keep existing)" : "https://hooks.slack.com/services/…"}
          style={{
            width: "100%", boxSizing: "border-box",
            border: "1px solid var(--primary-30)", borderRadius: "8px",
            padding: "9px 14px", fontFamily: "var(--fb)", fontSize: "14px", outline: "none",
          }}
        />
      </div>

      {/* Toggles */}
      <div style={{ display: "flex", gap: "24px", flexWrap: "wrap" }}>
        <Toggle checked={enableEmail} onChange={setEnableEmail} label="Send weekly email" />
        <Toggle checked={enableSlack} onChange={setEnableSlack} label="Post to Slack" />
      </div>

      {/* Timezone */}
      <div>
        <div style={{ fontFamily: "var(--fb)", fontSize: "12px", fontWeight: 600, letterSpacing: "2px", textTransform: "uppercase", color: "var(--mid)", marginBottom: "10px" }}>
          Timezone
        </div>
        <input
          value={tz}
          onChange={e => setTz(e.target.value)}
          placeholder="UTC"
          list="tz-suggestions"
          style={{
            width: "200px", border: "1px solid var(--primary-30)", borderRadius: "8px",
            padding: "9px 14px", fontFamily: "var(--fb)", fontSize: "14px", outline: "none",
          }}
        />
        <datalist id="tz-suggestions">
          {["UTC","America/New_York","America/Chicago","America/Los_Angeles","Europe/London","Europe/Paris","Asia/Tokyo","Asia/Singapore"].map(z => (
            <option key={z} value={z} />
          ))}
        </datalist>
      </div>

      {/* Save */}
      <button
        onClick={handleSave}
        disabled={mutation.isPending}
        style={{
          background: "var(--primary)", color: "#fff", border: "none", borderRadius: "8px",
          padding: "12px 28px", fontFamily: "var(--fb)", fontSize: "14px", fontWeight: 600,
          cursor: mutation.isPending ? "not-allowed" : "pointer", alignSelf: "flex-start",
          opacity: mutation.isPending ? 0.7 : 1,
        }}
      >
        {mutation.isPending ? "Saving…" : "Save Digest Config"}
      </button>
    </div>
  );
}
