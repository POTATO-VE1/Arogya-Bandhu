import { useEffect, useState } from "react";
import { api } from "./api";
import { C, Panel } from "./components";

type TwilioAccount = {
  name: string;
  from_number: string;
  allowlist_count: number;
  state: "ok" | "cooldown" | "unavailable";
  cooldown_remaining_s: number;
  last_seen: number;
  fail_count: number;
};

type TwilioHealth = {
  rotator_configured: boolean;
  message?: string;
  accounts: TwilioAccount[];
  global_allowlist_size?: number;
  public_base_url?: string;
};

const STATE_COLOR = {
  ok: C.success,
  cooldown: C.warning,
  unavailable: C.danger,
} as const;

function timeAgo(epoch: number): string {
  if (!epoch) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

export function TwilioHealth({ pollMs = 5000 }: { pollMs?: number }) {
  const [h, setH] = useState<TwilioHealth | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [failResult, setFailResult] = useState<any | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const r = await api<TwilioHealth>("/api/admin/twilio-health");
        if (!cancelled) {
          setH(r);
          setErr(null);
        }
      } catch (ex: any) {
        if (!cancelled) setErr(ex?.message || "load failed");
      }
    }
    load();
    const iv = setInterval(load, pollMs);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [pollMs, tick]);

  // refresh visible time-ago labels every 5s without re-fetching
  useEffect(() => {
    const iv = setInterval(() => setTick((t) => t + 1), 5000);
    return () => clearInterval(iv);
  }, []);

  async function runFailoverTest(failAcc: string) {
    setFailResult(null);
    try {
      const r = await api<any>("/api/admin/twilio-failover-test", {
        method: "POST",
        body: JSON.stringify({
          fail_account: failAcc,
          cooldown_seconds: 30,
        }),
      });
      setFailResult(r);
      setTick((t) => t + 1);
    } catch (ex: any) {
      setFailResult({ ok: false, error: ex?.message || "failed" });
    }
  }

  if (err) {
    return (
      <Panel title="Twilio Account Health" style={{ marginBottom: 16, borderColor: C.danger }}>
        <div style={{ color: C.danger, fontSize: "0.8125rem" }}>
          Cannot reach /api/admin/twilio-health: {err}
          <br />
          <span style={{ color: C.muted }}>
            (login as admin or superadmin to view this)
          </span>
        </div>
      </Panel>
    );
  }

  if (!h) {
    return (
      <Panel title="Twilio Account Health">
        <div style={{ color: C.muted, fontSize: "0.75rem" }}>loading…</div>
      </Panel>
    );
  }

  if (!h.rotator_configured) {
    return (
      <Panel title="Twilio Account Health" style={{ marginBottom: 16, borderColor: C.warning }}>
        <div style={{ color: C.warning, fontSize: "0.8125rem", marginBottom: 6 }}>
          [X] Twilio not configured
        </div>
        <div style={{ color: C.muted, fontSize: "0.75rem" }}>
          {h.message || "Set TWILIO_ACCOUNTS or the legacy TWILIO_* env vars on the server."}
        </div>
      </Panel>
    );
  }

  return (
    <Panel title="Twilio Account Health" style={{ marginBottom: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
        {h.accounts.map((a) => (
          <div
            key={a.name}
            style={{
              padding: 10,
              borderRadius: 4,
              border: `1px solid ${STATE_COLOR[a.state]}`,
              background: `${STATE_COLOR[a.state]}11`,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontWeight: 600, fontSize: "0.875rem" }}>{a.name}</span>
              <span style={{ fontSize: "0.6875rem", color: STATE_COLOR[a.state], textTransform: "uppercase", letterSpacing: "0.06em" }}>
                [{a.state}]
              </span>
            </div>
            <div style={{ fontSize: "0.6875rem", color: C.muted, marginTop: 4 }}>
              from <span style={{ fontFamily: "monospace" }}>{a.from_number}</span>
            </div>
            <div style={{ fontSize: "0.6875rem", color: C.muted }}>
              allowlist: {a.allowlist_count} nums
            </div>
            <div style={{ fontSize: "0.6875rem", color: C.muted }}>
              last seen: {timeAgo(a.last_seen)} {a.fail_count > 0 && <span style={{ color: C.danger }}>· fails: {a.fail_count}</span>}
            </div>
            {a.cooldown_remaining_s > 0 && (
              <div style={{ fontSize: "0.6875rem", color: C.warning, marginTop: 2 }}>
                cooldown: {a.cooldown_remaining_s}s
              </div>
            )}
            <button
              style={{
                marginTop: 8,
                fontFamily: "inherit", fontSize: "0.6875rem", padding: "3px 8px",
                background: "transparent", border: `1px solid ${C.border}`,
                color: C.secondary, borderRadius: 3, cursor: "pointer",
              }}
              onClick={() => runFailoverTest(a.name)}
              title={`Force a ${a.name} cooldown then place a test call to demonstrate rotation`}
            >
              [ demo rotation ]
            </button>
          </div>
        ))}
      </div>

      {failResult && (
        <div
          style={{
            marginTop: 12, padding: 10, borderRadius: 4,
            border: `1px solid ${failResult.ok ? C.success : C.danger}`,
            background: `${failResult.ok ? C.success : C.danger}11`,
            fontSize: "0.75rem",
          }}
        >
          <div style={{ fontWeight: 600, color: failResult.ok ? C.success : C.danger, marginBottom: 4 }}>
            {failResult.ok ? "[OK] Rotation demo" : "[X] Rotation demo failed"}
          </div>
          {failResult.failed_account && (
            <div style={{ color: C.muted }}>forced cooldown on: <strong>{failResult.failed_account}</strong> ({failResult.cooldown_seconds}s)</div>
          )}
          {failResult.target && <div style={{ color: C.muted }}>target number: <span style={{ fontFamily: "monospace" }}>{failResult.target}</span></div>}
          {failResult.tried && (
            <div style={{ marginTop: 6 }}>
              <div style={{ color: C.muted, marginBottom: 4 }}>tried accounts (in order):</div>
              {failResult.tried.map((t: any, i: number) => (
                <div key={i} style={{ paddingLeft: 8, fontSize: "0.75rem" }}>
                  {i + 1}. <span style={{ fontWeight: 600 }}>{t.name}</span> <span style={{ color: C.muted }}>({t.from})</span>{" "}
                  <span style={{ color: t.state === "ok" ? C.success : C.danger }}>[{t.state}]</span>
                </div>
              ))}
            </div>
          )}
          {failResult.winner && (
            <div style={{ marginTop: 6, color: C.success }}>
              winner: <strong>{failResult.winner.name}</strong> · call_sid: <span style={{ fontFamily: "monospace" }}>{failResult.winner.call_sid}</span>
            </div>
          )}
          {failResult.error && <div style={{ marginTop: 6, color: C.danger }}>{failResult.error}</div>}
        </div>
      )}
    </Panel>
  );
}
