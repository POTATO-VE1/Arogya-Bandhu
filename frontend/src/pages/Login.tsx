import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError, nowHHMM } from "../api";
import { useAuth } from "../App";
import { Button, C, Input } from "../components";

export function Login() {
  const { user, setUser } = useAuth();
  const nav = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (user) nav("/board", { replace: true });
  }, [user, nav]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const u = await api<{ id: string; display_name: string; role: string }>(
        "/api/auth/login",
        { method: "POST", body: JSON.stringify({ username, password }) },
      );
      setUser(u as any);
      nav("/board");
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: C.bg }}>
      {/* left — branding panel */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          padding: 48,
          borderRight: `1px solid ${C.borderMuted}`,
          background: C.surface,
          position: "relative",
          overflow: "hidden",
        }}
      >
        {/* subtle grid pattern */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundImage: `linear-gradient(${C.borderMuted} 1px, transparent 1px), linear-gradient(90deg, ${C.borderMuted} 1px, transparent 1px)`,
            backgroundSize: "48px 48px",
            opacity: 0.3,
          }}
        />
        <div style={{ position: "relative", zIndex: 1, textAlign: "center", maxWidth: 400 }}>
          <div style={{ fontSize: "2.5rem", fontWeight: 700, letterSpacing: "-0.02em", marginBottom: 8 }}>
            ▚▚ AAROGYA BANDHU
          </div>
          <div
            style={{
              fontSize: "1rem",
              color: C.muted,
              lineHeight: 1.6,
              marginBottom: 32,
            }}
          >
            discharge-to-recovery follow-up
            <br />
            for Karnataka government hospitals
          </div>

          {/* image / video placeholder */}
          <div
            style={{
              border: `2px dashed ${C.borderMuted}`,
              borderRadius: 4,
              padding: "40px 24px",
              color: C.disabled,
              fontSize: "0.8125rem",
            }}
          >
            logo / demo video
          </div>
        </div>
      </div>

      {/* right — login form */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          padding: 48,
        }}
      >
        <div style={{ width: "100%", maxWidth: 360 }}>
          {/* image / video placeholder */}
          <div
            style={{
              border: `2px dashed ${C.borderMuted}`,
              borderRadius: 4,
              padding: "32px 24px",
              textAlign: "center",
              color: C.disabled,
              fontSize: "0.8125rem",
              marginBottom: 24,
            }}
          >
            logo / screenshot
          </div>

          <div style={{ fontSize: "1.5rem", fontWeight: 600, marginBottom: 4 }}>Sign in</div>
          <div style={{ fontSize: "0.875rem", color: C.muted, marginBottom: 32 }}>
            to the discharge-to-recovery console
          </div>

          <form onSubmit={submit}>
            <Input
              label="username"
              value={username}
              autoCapitalize="none"
              autoComplete="username"
              placeholder="admin"
              onChange={(e) => setUsername(e.target.value)}
            />
            <Input
              label="password"
              type="password"
              value={password}
              autoComplete="current-password"
              placeholder="••••••••"
              onChange={(e) => setPassword(e.target.value)}
            />
            <Button type="submit" disabled={busy} style={{ width: "100%", marginTop: 8, padding: "12px 14px" }}>
              {busy ? "signing in…" : "[ sign in ]"}
            </Button>
          </form>

          {err && (
            <div
              style={{
                marginTop: 16,
                padding: "10px 12px",
                border: `1px solid ${C.danger}`,
                background: C.surface,
                color: C.danger,
                fontSize: "0.8125rem",
              }}
            >
              {nowHHMM()}{" "}
              {err}
            </div>
          )}


        </div>
      </div>
    </div>
  );
}
