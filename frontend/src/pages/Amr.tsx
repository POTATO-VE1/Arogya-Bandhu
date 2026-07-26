import { useEffect, useState } from "react";

import { api, ApiError, nowHHMM } from "../api";
import { C, LogLine, Panel, Stat } from "../components";

type AmrSummary = {
  total_enrolled: number;
  antibiotic_patients: number;
  aware_distribution: { Access: number; Watch: number; Reserve: number };
  calls_total: number;
  calls_completed: number;
  calls_no_answer: number;
  reach_rate: number;
  open_escalations: number;
  risk_distribution: { green: number; yellow: number; red: number };
  stewardship: {
    reminders_sent_today: number;
    pill_checks_sent: number;
    pill_responses: number;
    meds_confirmed: number;
  };
};

export function Amr() {
  const [s, setS] = useState<AmrSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [triggerResult, setTriggerResult] = useState<string | null>(null);

  async function refresh() {
    try {
      setS(await api<AmrSummary>("/api/amr/summary"));
      setErr(null);
    } catch (ex: any) {
      setErr(ex instanceof ApiError ? ex.message : "failed to load");
    }
  }
  useEffect(() => { refresh(); }, []);

  async function triggerSteward() {
    try {
      setTriggerResult("running…");
      const r = await api<{ reminders_sent: number; pill_checks_sent: number; non_adherence_escalations: number }>(
        "/api/amr/steward/trigger", { method: "POST" },
      );
      setTriggerResult(
        `sent ${r.reminders_sent} reminder(s), ${r.pill_checks_sent} pill check(s), ${r.non_adherence_escalations} escalation(s)`
      );
      refresh();
    } catch (ex) {
      setTriggerResult(null);
      setErr(ex instanceof ApiError ? ex.message : "trigger failed");
    }
  }

  if (err) return <Panel><LogLine tone="danger">{err}</LogLine></Panel>;
  if (!s) return <Panel><div style={{ color: C.muted }} className="loading-pulse">loading…</div></Panel>;

  const totalAbx = s.aware_distribution.Access + s.aware_distribution.Watch + s.aware_distribution.Reserve;

  return (
    <div>
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 16 }}>
        amr stewardship dashboard
      </h2>

      {/* KPI row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
        <Stat value={s.total_enrolled} label="active enrollments" />
        <Stat value={s.antibiotic_patients} label="antibiotic patients" />
        <Stat value={`${s.reach_rate}%`} label="reach rate" />
        <Stat
          value={<span style={{ color: s.open_escalations ? C.danger : C.text }}>{s.open_escalations}</span>}
          label="open escalations"
        />
      </div>

      {/* AWaRe distribution */}
      <Panel title="AWaRe antibiotic classification" style={{ marginBottom: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
          <div style={{ padding: 12, border: `1px solid ${C.border}`, borderRadius: 4 }}>
            <div style={{ color: C.success, fontSize: "1.5rem", fontWeight: 600 }}>{s.aware_distribution.Access}</div>
            <div style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em" }}>
              Access · {totalAbx ? Math.round(s.aware_distribution.Access / totalAbx * 100) : 0}%
            </div>
            <div style={{ color: C.muted, fontSize: "0.75rem", marginTop: 4 }}>first-line, widely available</div>
          </div>
          <div style={{ padding: 12, border: `1px solid ${C.border}`, borderRadius: 4 }}>
            <div style={{ color: C.warning, fontSize: "1.5rem", fontWeight: 600 }}>{s.aware_distribution.Watch}</div>
            <div style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em" }}>
              Watch · {totalAbx ? Math.round(s.aware_distribution.Watch / totalAbx * 100) : 0}%
            </div>
            <div style={{ color: C.muted, fontSize: "0.75rem", marginTop: 4 }}>higher resistance potential</div>
          </div>
          <div style={{ padding: 12, border: `1px solid ${C.border}`, borderRadius: 4 }}>
            <div style={{ color: C.danger, fontSize: "1.5rem", fontWeight: 600 }}>{s.aware_distribution.Reserve}</div>
            <div style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em" }}>
              Reserve · {totalAbx ? Math.round(s.aware_distribution.Reserve / totalAbx * 100) : 0}%
            </div>
            <div style={{ color: C.muted, fontSize: "0.75rem", marginTop: 4 }}>last resort, critical resistance</div>
          </div>
        </div>
      </Panel>

      {/* Call stats + risk distribution */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
        <Panel title="call statistics">
          <div style={{ fontSize: "0.8125rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: `1px solid ${C.borderMuted}` }}>
              <span>total calls</span><span style={{ fontWeight: 600 }}>{s.calls_total}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: `1px solid ${C.borderMuted}` }}>
              <span>completed</span><span style={{ color: C.success, fontWeight: 600 }}>{s.calls_completed}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0" }}>
              <span>no answer / failed</span><span style={{ color: C.warning, fontWeight: 600 }}>{s.calls_no_answer}</span>
            </div>
          </div>
        </Panel>

        <Panel title="risk distribution">
          <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
            <div style={{ flex: 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                <span style={{ color: C.success, fontSize: "1.25rem", fontWeight: 600 }}>{s.risk_distribution.green}</span>
                <span style={{ color: C.muted, fontSize: "0.8125rem" }}>green</span>
                <div style={{ flex: 1, height: 8, background: C.border, borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: `${s.calls_completed ? (s.risk_distribution.green / s.calls_completed * 100) : 0}%`, height: "100%", background: C.success }} />
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                <span style={{ color: C.warning, fontSize: "1.25rem", fontWeight: 600 }}>{s.risk_distribution.yellow}</span>
                <span style={{ color: C.muted, fontSize: "0.8125rem" }}>yellow</span>
                <div style={{ flex: 1, height: 8, background: C.border, borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: `${s.calls_completed ? (s.risk_distribution.yellow / s.calls_completed * 100) : 0}%`, height: "100%", background: C.warning }} />
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ color: C.danger, fontSize: "1.25rem", fontWeight: 600 }}>{s.risk_distribution.red}</span>
                <span style={{ color: C.muted, fontSize: "0.8125rem" }}>red</span>
                <div style={{ flex: 1, height: 8, background: C.border, borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: `${s.calls_completed ? (s.risk_distribution.red / s.calls_completed * 100) : 0}%`, height: "100%", background: C.danger }} />
                </div>
              </div>
            </div>
          </div>
        </Panel>
      </div>

      {/* Stewardship status */}
      <Panel title="stewardship activity" style={{ marginBottom: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
          <Stat value={s.stewardship.reminders_sent_today} label="reminders sent" />
          <Stat value={s.stewardship.pill_checks_sent} label="pill checks sent" />
          <Stat value={s.stewardship.pill_responses} label="pill responses" />
          <Stat value={s.stewardship.meds_confirmed} label="meds confirmed" />
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            style={{
              fontFamily: "inherit", fontSize: "0.8125rem", background: "transparent",
              border: `1px solid ${C.accent}`, color: C.accent, borderRadius: 4,
              padding: "8px 16px", cursor: "pointer",
            }}
            onClick={triggerSteward}
          >
            [ run full steward cycle ]
          </button>
          {triggerResult && <span style={{ color: C.muted, fontSize: "0.75rem" }}>{triggerResult}</span>}
        </div>
      </Panel>

      <div style={{ color: C.muted, fontSize: "0.75rem" }}>
        {nowHHMM()} · stewardship jobs run daily at 09:00 IST · weekly summary on Mondays
      </div>
    </div>
  );
}
