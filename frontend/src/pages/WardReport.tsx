import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "../api";
import { C, Panel, Stat, LogLine } from "../components";
import { t } from "../i18n";

type Day = { day_index: number; completed: number; total: number };
type WardSummary = {
  ward: string | null;
  period_days: number;
  total_enrolled: number;
  active: number;
  completed: number;
  cancelled: number;
  reach_rate: number | null;
  red_flag_rate: number | null;
  open_escalations: number;
  avg_ack_hours: number | null;
  outcome_breakdown: Record<string, number>;
  call_completion_by_day: Day[];
};

export function WardReport() {
  const nav = useNavigate();
  const [data, setData] = useState<WardSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [days, setDays] = useState(7);

  useEffect(() => {
    setErr(null);
    api<WardSummary>(`/api/analytics/ward-summary?days=${days}`)
      .then(setData)
      .catch((ex) => setErr(ex instanceof ApiError ? ex.message : "load failed"));
  }, [days]);

  if (err) return <Panel><LogLine tone="danger">{err}</LogLine></Panel>;
  if (!data) return <Panel><div style={{ color: C.muted }}>...</div></Panel>;

  return (
    <div>
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 16 }}>
        {t("ward_report")}{data.ward ? ` · ${data.ward}` : ""}
      </h2>

      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <span style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.08em" }}>Period</span>
        {[7, 14, 30].map((d) => (
          <button
            key={d}
            onClick={() => setDays(d)}
            style={{
              padding: "4px 10px", fontFamily: "inherit", fontSize: "0.75rem",
              background: d === days ? C.accent : C.elevated,
              color: d === days ? "#fff" : C.text,
              border: `1px solid ${d === days ? C.accent : C.border}`,
              borderRadius: 4, cursor: "pointer",
            }}
          >last {d} days</button>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
        <Stat value={data.total_enrolled} label="ENROLLED" />
        <Stat value={data.reach_rate !== null ? `${Math.round(data.reach_rate * 100)}%` : "—"} label="REACH" />
        <Stat value={<span style={{ color: data.open_escalations ? C.danger : C.text }}>{data.open_escalations}</span>} label="OPEN ESC" />
        <Stat value={data.red_flag_rate !== null ? `${Math.round(data.red_flag_rate * 100)}%` : "—"} label="RED FLAG RATE" />
      </div>

      <Panel title="STATUS BREAKDOWN" style={{ marginBottom: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, fontSize: "0.875rem" }}>
          <div><span style={{ color: C.muted }}>Active:</span> <strong>{data.active}</strong></div>
          <div><span style={{ color: C.muted }}>Completed:</span> <strong>{data.completed}</strong></div>
          <div><span style={{ color: C.muted }}>Cancelled:</span> <strong>{data.cancelled}</strong></div>
        </div>
        {Object.keys(data.outcome_breakdown).length > 0 && (
          <div style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.borderMuted}` }}>
            <div style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4 }}>OUTCOMES</div>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: "0.8125rem" }}>
              {Object.entries(data.outcome_breakdown).map(([k, v]) => (
                <span key={k}>
                  <span style={{ color: C.muted }}>{k}:</span> <strong>{v}</strong>
                </span>
              ))}
            </div>
          </div>
        )}
        {data.avg_ack_hours !== null && (
          <div style={{ marginTop: 12, fontSize: "0.8125rem" }}>
            <span style={{ color: C.muted }}>Avg ack time:</span>{" "}
            <strong>{data.avg_ack_hours}h</strong>
          </div>
        )}
      </Panel>

      {data.call_completion_by_day.length > 0 && (
        <Panel title="CALL COMPLETION BY DAY" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {data.call_completion_by_day.map((d) => {
              const pct = d.total > 0 ? d.completed / d.total : 0;
              return (
                <div key={d.day_index} style={{ display: "flex", alignItems: "center", gap: 12, fontSize: "0.8125rem" }}>
                  <span style={{ width: 60, color: C.muted }}>Day {d.day_index}</span>
                  <div style={{ flex: 1, height: 16, background: C.elevated, borderRadius: 3, overflow: "hidden" }}>
                    <div style={{ width: `${pct * 100}%`, height: "100%", background: pct >= 0.7 ? C.success : pct >= 0.4 ? C.warning : C.danger }} />
                  </div>
                  <span style={{ width: 80, textAlign: "right" }}>{d.completed}/{d.total} ({Math.round(pct * 100)}%)</span>
                </div>
              );
            })}
          </div>
        </Panel>
      )}

      <button onClick={() => nav("/board")} style={{
        background: "transparent", border: `1px solid ${C.border}`,
        color: C.muted, padding: "6px 12px", borderRadius: 4, cursor: "pointer",
        fontFamily: "inherit", fontSize: "0.75rem",
      }}>[ back to board ]</button>
    </div>
  );
}
