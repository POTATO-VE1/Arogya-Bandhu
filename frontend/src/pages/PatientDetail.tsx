import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { api, ApiError, nowHHMM } from "../api";
import { Button, C, LogLine, Panel, RiskBadge } from "../components";
import { t } from "../i18n";

type TimelineEvent = {
  type: string;
  actor: string | null;
  actor_name: string | null;
  timestamp: string;
  detail: string;
  risk?: string | null;
  acked_by?: string | null;
  resolved_by?: string | null;
};

type TimelineData = {
  patient: { id: string; name: string; age: number | null; sex: string | null; phone: string };
  timeline: TimelineEvent[];
};

type ProtoQuestion = {
  clip: string;
  options: Record<string, { reason: string; score: number }>;
};
type ProtoDetail = { id: string; name_en: string; name_kn: string; questions: Record<string, ProtoQuestion> };

type Enroll = {
  id: string;
  protocol_id: string;
  condition_label: string;
  ward: string | null;
  status: string;
  number_verified: boolean;
  meds: { name: string; type: string; doses: number }[];
  calls: {
    id: string;
    day_index: number;
    status: string;
    risk: string | null;
    risk_reasons: string | null;
    scheduled_at: string;
    provider: string | null;
    responses: { node_id: string; digit: string; score: number }[];
  }[];
  escalations: { id: string; level: string; status: string; reasons: string; created_at: string }[];
};
type Patient = {
  id: string;
  name: string;
  age: number | null;
  sex: string | null;
  caregiver_name: string;
  caregiver_phone: string;
  abha_number: string | null;
  enrollments: Enroll[];
};
type HealthData = {
  connected: boolean;
  last_synced: string | null;
  data_points: number;
  metrics: { metric_type: string; latest: number; unit: string; avg_7d: number; count: number; trend: string }[];
  summary: Record<string, any>;
};

function resolveLabel(proto: ProtoDetail | null, nodeId: string, digit: string): string {
  if (!proto) return digit;
  const q = proto.questions[nodeId];
  if (!q) return digit;
  const opt = q.options[digit];
  return opt?.reason || digit;
}

export function PatientDetail() {
  const { id } = useParams<{ id: string }>();
  const [p, setP] = useState<Patient | null>(null);
  const [hd, setHd] = useState<HealthData | null>(null);
  const [proto, setProto] = useState<ProtoDetail | null>(null);
  const [tl, setTl] = useState<TimelineEvent[]>([]);
  const [err, setErr] = useState<string | null>(null);

  async function refresh() {
    try {
      setP(await api<Patient>(`/api/patients/${id}`));
      setErr(null);
    } catch (ex: any) {
      setErr(ex instanceof ApiError ? (ex.status === 404 ? t("patient_not_found") : ex.message) : "failed");
    }
  }
  useEffect(() => { refresh(); }, [id]);

  useEffect(() => {
    if (!id) return;
    api<HealthData>(`/api/patients/${id}/health-data`).then(setHd).catch(() => {});
    api<TimelineData>(`/api/patients/${id}/timeline`).then((d) => setTl(d.timeline || [])).catch(() => {});
  }, [id]);

  // fetch protocol detail for response labels
  useEffect(() => {
    if (!p?.enrollments?.[0]?.protocol_id) return;
    const pid = p.enrollments[0].protocol_id;
    api<ProtoDetail>(`/api/protocols/${pid}/detail`).then(setProto).catch(() => {});
  }, [p?.enrollments?.[0]?.protocol_id]);

  async function trigger(eid: string, channel: "twilio" | "sim") {
    try {
      const r = await api<{ call_id: string }>("/api/demo/trigger-call", {
        method: "POST", body: JSON.stringify({ enrollment_id: eid, channel }),
      });
      if (channel === "sim") location.href = `/demo?call=${r.call_id}`;
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.message : "trigger failed");
    }
  }

  if (err) return <Panel><LogLine tone="danger">{err}</LogLine></Panel>;
  if (!p) return <Panel><div style={{ color: C.muted }}>…</div></Panel>;
  const en = p.enrollments[0];
  if (!en) return (
    <div>
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 4 }}>{p.name}</h2>
      <div style={{ color: C.muted, fontSize: "0.8125rem", marginBottom: 16 }}>
        {t("caregiver")} {p.caregiver_name} · {p.age ?? "?"}{p.sex ?? ""} · {t("no_enrollments_yet")}
      </div>
      <Panel><div style={{ color: C.muted }}>{t("no_active_enrollments")}</div></Panel>
    </div>
  );

  // ── adherence timeline data ──
  const sortedCalls = [...en.calls].sort((a, b) => a.day_index - b.day_index);
  const adherenceBlocks = sortedCalls.map((c) => {
    const hasScore = c.responses.some((r) => r.score > 0);
    const noResponse = c.status === "no_answer" || c.status === "failed" || c.status === "pending";
    let label: string;
    let color: string;
    let symbol: string;
    if (noResponse) {
      label = t("no_answer_title");
      color = C.borderMuted;
      symbol = "?";
    } else if (hasScore) {
      label = t("symptoms_reported_label");
      color = c.risk === "red" ? C.danger : C.warning;
      symbol = "!";
    } else {
      label = t("all_clear_label");
      color = C.success;
      symbol = "[Y]";
    }
    return { day: c.day_index, status: c.status, label, color, symbol, risk: c.risk };
  });

  return (
    <div className="print-content">
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 4 }}>
        {p.name}{" "}
        <RiskBadge level={en?.calls?.slice().reverse().find((c) => c.risk)?.risk || null} />
      </h2>
      <div style={{ color: C.muted, fontSize: "0.8125rem", marginBottom: 16 }}>
        {t("caregiver")} {p.caregiver_name} · {p.age ?? "?"}{p.sex ?? ""} · {t("ward")} {en?.ward || "—"} ·{" "}
        {en?.escalations?.length ? `${en.escalations.length} ${t("escalation_s")}` : t("no_escalations")} ·{" "}
        {p.abha_number ? `ABHA ${p.abha_number}` : t("no_abha")}
      </div>

      {/* ── Patient Overview & Diet ── */}
      <Panel title={t("patient_clinical_overview")} style={{ marginBottom: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, fontSize: "0.8125rem" }}>
          <div>
            <div style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase" }}>{t("condition_heading")}</div>
            <div style={{ fontWeight: 600, marginTop: 2 }}>{en?.condition_label || t("general_recovery")}</div>
          </div>
          <div>
            <div style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase" }}>{t("caregiver_phone_heading")}</div>
            <div style={{ fontWeight: 600, marginTop: 2 }}>{p.caregiver_phone}</div>
          </div>
          <div>
            <div style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase" }}>{t("protocol_label")}</div>
            <div style={{ fontWeight: 600, marginTop: 2 }}>{en?.protocol_id || t("general")}</div>
          </div>
        </div>
      </Panel>

      {/* ── Adherence Timeline ── */}
      {adherenceBlocks.length > 0 && (
        <Panel title={t("medication_adherence")} style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
            {adherenceBlocks.map((b, i) => (
              <div key={i} style={{ textAlign: "center" }}>
                <div
                  title={`D${b.day}: ${b.label}`}
                  style={{
                    width: 40, height: 40, borderRadius: 6,
                    background: `${b.color}22`, border: `2px solid ${b.color}`,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: "1.125rem", fontWeight: 700, color: b.color,
                    cursor: "default",
                  }}
                >
                  {b.symbol}
                </div>
                <div style={{ fontSize: "0.625rem", color: C.muted, marginTop: 2 }}>D{b.day}</div>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", gap: 16, fontSize: "0.75rem", color: C.muted }}>
            <span>{t("all_clear")}</span>
            <span style={{ color: C.warning }}>{t("symptoms_reported")}</span>
            <span>{t("no_answer_label")}</span>
          </div>
        </Panel>
      )}

      {/* ── Medications ── */}
      <Panel title={t("medications")} style={{ marginBottom: 16 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8125rem" }}>
          <thead>
            <tr style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em" }}>
              <Th>{t("name")}</Th><Th>{t("type")}</Th><Th>{t("doses_day")}</Th>
            </tr>
          </thead>
          <tbody>
            {en.meds.map((m, i) => (
              <tr key={i} style={{ borderTop: `1px solid ${C.borderMuted}` }}>
                <Td>{m.name}</Td><Td>{m.type}</Td>
                <Td>{m.doses}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      {/* ── Call Timeline with Response Labels ── */}
      <Panel title={t("call_timeline")} style={{ marginBottom: 16 }}>
        {en.calls.length === 0 ? <div style={{ color: C.muted }}>—</div> :
          en.calls.map((c) => (
            <div key={c.id} style={{ borderTop: `1px solid ${C.borderMuted}`, padding: "8px 0" }}>
              <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
                <span>
                  <strong>D{c.day_index}</strong> · {c.status} · {c.provider || "—"} ·{" "}
                  <RiskBadge level={c.risk} />
                </span>
                <span style={{ color: C.muted, fontSize: "0.75rem" }}>
                  {new Date(c.scheduled_at).toLocaleString()}
                </span>
              </div>
              {c.responses.map((r, i) => (
                <div key={i} style={{ color: C.secondary, fontSize: "0.8125rem", paddingLeft: 12 }}>
                  ◂ {resolveLabel(proto, r.node_id, r.digit)}
                  {r.score > 0 && <span style={{ color: r.score >= 10 ? C.danger : C.warning }}> ({t("score")} {r.score})</span>}
                </div>
              ))}
              {c.risk_reasons && (
                <div style={{ color: c.risk === "red" ? C.danger : C.muted, fontSize: "0.75rem", paddingLeft: 12 }}>
                  {t("reasons")} {c.risk_reasons}
                </div>
              )}
            </div>
          ))
        }
      </Panel>

      {/* ── Health Metrics ── */}
      {hd && hd.connected && hd.data_points > 0 && (
        <Panel title={t("health_metrics")} style={{ marginBottom: 16 }}>
          <div style={{ color: C.muted, fontSize: "0.75rem", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.1em" }}>
            {hd.data_points} {t("data_points")} · {t("last_synced")} {hd.last_synced ? new Date(hd.last_synced).toLocaleDateString() : "—"}
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8125rem" }}>
            <thead>
              <tr style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em" }}>
                <Th>{t("metric_label")}</Th><Th>{t("latest")}</Th><Th>{t("avg_7d")}</Th><Th>{t("trend")}</Th><Th>{t("samples")}</Th>
              </tr>
            </thead>
            <tbody>
              {hd.metrics.map((m, i) => (
                <tr key={i} style={{ borderTop: `1px solid ${C.borderMuted}` }}>
                  <Td>{m.metric_type}</Td>
                  <Td><strong>{m.latest}</strong> {m.unit}</Td>
                  <Td>{m.avg_7d} {m.unit}</Td>
                  <Td>
                    <span style={{ color: m.trend === "improving" ? C.success : m.trend === "declining" ? C.danger : C.muted }}>
                      {m.trend === "improving" ? t("improving") : m.trend === "declining" ? t("declining") : t("stable")}
                    </span>
                  </Td>
                  <Td style={{ color: C.muted }}>{m.count}</Td>
                </tr>
              ))}
            </tbody>
          </table>
          {hd.summary?.anomalies?.length > 0 && (
            <div style={{ marginTop: 8, borderTop: `1px solid ${C.borderMuted}`, paddingTop: 8 }}>
              {hd.summary.anomalies.map((a: string, i: number) => (
                <LogLine key={i} tone="warning">[!] {a}</LogLine>
              ))}
            </div>
          )}
          <div style={{ marginTop: 8, borderTop: `1px solid ${C.borderMuted}`, paddingTop: 8 }}>
            <span style={{ fontSize: "0.75rem", color: C.muted }}>{t("composite_score")} </span>
            <strong style={{ color: hd.summary?.health_score >= 70 ? C.success : hd.summary?.health_score >= 40 ? C.warning : C.danger }}>
              {hd.summary?.health_score ?? "—"}/100
            </strong>
          </div>
        </Panel>
      )}

      {/* ── Activity Timeline ── */}
      {tl.length > 0 && (
        <Panel title={t("activity_timeline")} style={{ marginBottom: 16 }}>
          <div style={{ position: "relative", paddingLeft: 20 }}>
            {/* vertical line */}
            <div style={{ position: "absolute", left: 7, top: 6, bottom: 6, width: 2, background: C.borderMuted }} />
            {tl.map((ev, i) => {
              const dotColor = getDotColor(ev);
              const icon = getEventIcon(ev);
              return (
                <div key={i} style={{ position: "relative", paddingBottom: i < tl.length - 1 ? 16 : 0 }}>
                  {/* dot */}
                  <div style={{
                    position: "absolute", left: -20, top: 3,
                    width: 12, height: 12, borderRadius: "50%",
                    background: dotColor, border: `2px solid ${C.surface}`,
                    zIndex: 1,
                  }} />
                  <div style={{ fontSize: "0.8125rem" }}>
                    <span style={{ marginRight: 6 }}>{icon}</span>
                    <span>{ev.detail}</span>
                  </div>
                  <div style={{ display: "flex", gap: 8, marginTop: 2, fontSize: "0.6875rem", color: C.muted }}>
                    {ev.actor_name && <span>{ev.actor_name}</span>}
                    <span>{relativeTime(ev.timestamp)}</span>
                    {ev.risk && <span><RiskBadge level={ev.risk} /></span>}
                    {ev.acked_by && <span>acked by {ev.acked_by}</span>}
                    {ev.resolved_by && <span>resolved by {ev.resolved_by}</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>
      )}

      {/* ── Actions ── */}
      <Panel title={t("actions")} style={{ marginTop: 16 }}>
        <Button variant="ghost" onClick={() => en && trigger(en.id, "sim")}>{t("demo_call_sim")}</Button>{" "}
        <Button variant="ghost" onClick={() => en && trigger(en.id, "twilio")}>{t("trigger_real")}</Button>{" "}
        {en && <a href={`/api/patients/${p.id}/fhir`} target="_blank" rel="noreferrer"><Button variant="ghost">{t("fhir_json")}</Button></a>}{" "}
        {en && <a href={`/sheet/${en.id}`} target="_blank" rel="noreferrer"><Button variant="ghost">{t("kannada_sheet_btn")}</Button></a>}{" "}
        <a href={`/print/patient/${p.id}`} target="_blank" rel="noreferrer"><Button variant="ghost">{t("print_summary_btn")}</Button></a>
        {err && <LogLine tone="danger">{nowHHMM()} {err}</LogLine>}
      </Panel>
    </div>
  );
}

function getDotColor(ev: TimelineEvent): string {
  switch (ev.type) {
    case "patient_created": return C.success;
    case "enrollment": return C.accent;
    case "call": return ev.risk === "red" ? C.danger : ev.risk === "yellow" ? C.warning : C.success;
    case "escalation": return C.danger;
    case "outcome": return "#a855f7";
    default: return C.muted;
  }
}

function getEventIcon(ev: TimelineEvent): string {
  switch (ev.type) {
    case "patient_created": return "+";
    case "enrollment": return "[+]";
    case "call": return ev.risk === "red" ? "[R]" : ev.risk === "yellow" ? "[Y]" : "[G]";
    case "escalation": return "[!]";
    case "outcome": return "[*]";
    default: return "●";
  }
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

const Th = ({ children }: any) => (<th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>{children}</th>);
const Td = ({ children, style }: any) => (<td style={{ padding: "6px 8px", ...style }}>{children}</td>);
