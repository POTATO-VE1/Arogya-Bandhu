import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "../api";
import { useAuth } from "../App";
import { Button, C, Input } from "../components";

export function Login() {
  const { user, setUser } = useAuth();
  const nav = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // T10: forgot-password flow
  const [forgotMode, setForgotMode] = useState<"login" | "forgot" | "reset">("login");
  const [forgotHint, setForgotHint] = useState<string | null>(null);
  const [otp, setOtp] = useState("");
  const [newPassword, setNewPassword] = useState("");

  useEffect(() => {
    if (user && forgotMode === "login") nav("/board", { replace: true });
  }, [user, nav, forgotMode]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      const me = await api("/api/auth/me");
      setUser(me as any);
      nav("/board", { replace: true });
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  async function requestOtp() {
    if (!username) { setErr("enter your username first"); return; }
    setErr(null); setForgotHint(null); setBusy(true);
    try {
      const r = await api<{ ok: boolean; hint: string }>("/api/auth/forgot",
        { method: "POST", body: JSON.stringify({ username }) });
      setForgotHint(r.hint);
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.message : "request failed");
    } finally {
      setBusy(false);
    }
  }

  async function submitReset(e: React.FormEvent) {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      await api("/api/auth/reset", {
        method: "POST",
        body: JSON.stringify({ username, otp, new_password: newPassword }),
      });
      // Auto-login with the new password
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password: newPassword }),
      });
      const me = await api("/api/auth/me");
      setUser(me as any);
      nav("/board", { replace: true });
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.message : "reset failed");
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
          <div style={{ fontSize: "1rem", color: C.muted, lineHeight: 1.6 }}>
            discharge-to-recovery follow-up
            <br />
            for Karnataka government hospitals
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
          {/* Logo */}
          <div style={{ fontSize: "2.5rem", fontWeight: 700, marginBottom: 24, letterSpacing: "-0.02em" }}>
            ▚▚
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

          <div style={{ marginTop: 12, textAlign: "center" }}>
            <button
              type="button"
              onClick={() => { setForgotMode("forgot"); setErr(null); setForgotHint(null); }}
              style={{ background: "transparent", border: "none", color: C.accent, cursor: "pointer", fontFamily: "inherit", fontSize: "0.75rem" }}
            >
              [ forgot password? ]
            </button>
          </div>

          {err && (
            <div
              style={{
                marginTop: 16,
                padding: "10px 12px",
                borderRadius: 6,
                border: `1px solid ${C.danger}`,
                background: "rgba(239, 68, 68, 0.1)",
                color: C.danger,
                fontSize: "0.8125rem",
              }}
            >
              {err}
            </div>
          )}

          <div style={{ marginTop: 24, paddingTop: 16, borderTop: `1px solid ${C.borderMuted}` }}>
            <div style={{ fontSize: "0.75rem", color: C.muted, marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 600 }}>
              Quick Demo Sign-In
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Button
                type="button"
                variant="ghost"
                style={{ fontSize: "0.75rem", padding: "6px 10px" }}
                onClick={() => { setUsername("admin"); setPassword("admin123"); }}
              >
                Admin
              </Button>
            </div>
            <div style={{ fontSize: "0.6875rem", color: C.muted, marginTop: 8 }}>
              Staff accounts are created via the Telegram admin bot.
            </div>
          </div>
        </div>
      </div>

      {/* T10: forgot-password modal */}
      {forgotMode === "forgot" && (
        <div
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}
          onClick={() => setForgotMode("login")}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: 24, width: 360, maxWidth: "92vw" }}
          >
            <div style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>
              Forgot password
            </div>
            <div style={{ fontSize: "0.8125rem", color: C.text, marginBottom: 12 }}>
              Enter your username. An OTP will be sent to the admin's Telegram
              chat; the admin will relay the code to you verbally.
            </div>
            <Input
              label="username"
              value={username}
              autoFocus
              autoComplete="username"
              onChange={(e) => setUsername(e.target.value)}
            />
            {forgotHint && (
              <div style={{ padding: 10, background: C.elevated, border: `1px solid ${C.success}`, borderRadius: 4, color: C.success, fontSize: "0.75rem", marginBottom: 8 }}>
                {forgotHint}
              </div>
            )}
            {err && <div style={{ color: C.danger, fontSize: "0.75rem", marginBottom: 8 }}>{err}</div>}
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <Button
                variant="ghost"
                style={{ flex: 1 }}
                onClick={() => { setForgotMode("login"); setForgotHint(null); setErr(null); }}
              >
                [ back ]
              </Button>
              <Button
                style={{ flex: 1 }}
                onClick={() => { requestOtp(); setForgotMode("reset"); }}
                disabled={busy || !username}
              >
                {busy ? "sending…" : "[ send OTP ]"}
              </Button>
            </div>
            {forgotHint && (
              <div style={{ marginTop: 12, textAlign: "center" }}>
                <button
                  type="button"
                  onClick={() => setForgotMode("reset")}
                  style={{ background: "transparent", border: "none", color: C.accent, cursor: "pointer", fontFamily: "inherit", fontSize: "0.75rem" }}
                >
                  [ I have the code — reset now ]
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {forgotMode === "reset" && (
        <div
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}
          onClick={() => setForgotMode("login")}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: 24, width: 360, maxWidth: "92vw" }}
          >
            <div style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>
              Reset password
            </div>
            <div style={{ fontSize: "0.8125rem", color: C.muted, marginBottom: 12 }}>
              Username: <strong style={{ color: C.text }}>{username}</strong>
            </div>
            <form onSubmit={submitReset}>
              <Input
                label="6-digit OTP from admin"
                value={otp}
                autoFocus
                onChange={(e) => setOtp(e.target.value)}
              />
              <Input
                label="new password (min 6 chars)"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
              {err && <div style={{ color: C.danger, fontSize: "0.75rem", marginBottom: 8 }}>{err}</div>}
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <Button
                  type="button"
                  variant="ghost"
                  style={{ flex: 1 }}
                  onClick={() => setForgotMode("login")}
                >
                  [ cancel ]
                </Button>
                <Button
                  type="submit"
                  style={{ flex: 1 }}
                  disabled={busy || otp.length !== 6 || newPassword.length < 6}
                >
                  {busy ? "resetting…" : "[ reset & sign in ]"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
