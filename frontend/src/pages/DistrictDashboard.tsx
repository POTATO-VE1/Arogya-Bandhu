import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "../api";
import { C, Panel, Stat, LogLine } from "../components";

type WardBreakdown = {
  ward: string; enrolled: number; active: number; completed: number;
  cancelled: number; red: number; reach_rate: number; red_rate: number;
};
type ProtoBreakdown = {
  protocol: string; enrolled: number;
  completion_rate: number; red_rate: number;
};
type DistrictDashboard = {
  total_enrolled: number; total_active: number; total_red: number;
  period_days: number;
  ward_breakdown: WardBreakdown[];
  protocol_breakdown: ProtoBreakdown[];
  top_escalation_reasons: { reason: string; count: number }[];
};

export function DistrictDashboard() {
  const nav = useNavigate();
  const [data, setData] = useState<DistrictDashboard | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [days, setDays] = useState(30);

  useEffect(() => {
    setErr(null);
    api<DistrictDashboard>(`/api/analytics/district-dashboard?days=${days}`)
      .then(setData)
      .catch((ex) => setErr(ex instanceof ApiError ? ex.message : "load failed"));
  }, [days]);

  if (err) return <Panel><LogLine tone="danger">{err}</LogLine></Panel>;
  if (!data) return <Panel><div style={{ color: C.muted }}>...</div></Panel>;

  return (
    <div>
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 16 }}>DISTRICT DASHBOARD</h2>

      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <span style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.08em" }}>Period</span>
        {[7, 30, 90].map((d) => (
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

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 16 }}>
        <Stat value={data.total_enrolled} label="TOTAL ENROLLED" />
        <Stat value={data.total_active} label="ACTIVE" />
        <Stat value={<span style={{ color: data.total_red ? C.danger : C.text }}>{data.total_red}</span>} label="RED FLAGS" />
      </div>

      <Panel title="PER-WARD BREAKDOWN" style={{ marginBottom: 16 }}>
        {data.ward_breakdown.length === 0 ? (
          <div style={{ color: C.muted, fontSize: "0.8125rem" }}>no data in this period</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8125rem" }}>
            <thead>
              <tr style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>Ward</th>
                <th style={{ textAlign: "right", padding: "6px 8px" }}>Enrolled</th>
                <th style={{ textAlign: "right", padding: "6px 8px" }}>Active</th>
                <th style={{ textAlign: "right", padding: "6px 8px" }}>Red</th>
                <th style={{ textAlign: "right", padding: "6px 8px" }}>Reach</th>
              </tr>
            </thead>
            <tbody>
              {data.ward_breakdown.map((w) => (
                <tr key={w.ward} style={{ borderTop: `1px solid ${C.borderMuted}` }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{w.ward}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{w.enrolled}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{w.active}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right", color: w.red ? C.danger : C.muted }}>{w.red}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{Math.round(w.reach_rate * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="PER-PROTOCOL BREAKDOWN" style={{ marginBottom: 16 }}>
        {data.protocol_breakdown.length === 0 ? (
          <div style={{ color: C.muted, fontSize: "0.8125rem" }}>no data</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8125rem" }}>
            <thead>
              <tr style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>Protocol</th>
                <th style={{ textAlign: "right", padding: "6px 8px" }}>Enrolled</th>
                <th style={{ textAlign: "right", padding: "6px 8px" }}>Completion</th>
                <th style={{ textAlign: "right", padding: "6px 8px" }}>Red rate</th>
              </tr>
            </thead>
            <tbody>
              {data.protocol_breakdown.map((p) => (
                <tr key={p.protocol} style={{ borderTop: `1px solid ${C.borderMuted}` }}>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{p.protocol}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{p.enrolled}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>{Math.round(p.completion_rate * 100)}%</td>
                  <td style={{ padding: "6px 8px", textAlign: "right", color: p.red_rate > 0.1 ? C.warning : C.muted }}>{Math.round(p.red_rate * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {data.top_escalation_reasons.length > 0 && (
        <Panel title="TOP ESCALATION REASONS" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {data.top_escalation_reasons.map((r, i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: "0.8125rem", borderTop: i > 0 ? `1px solid ${C.borderMuted}` : "none" }}>
                <span>{r.reason}</span>
                <span style={{ color: C.danger, fontWeight: 600 }}>{r.count}</span>
              </div>
            ))}
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
