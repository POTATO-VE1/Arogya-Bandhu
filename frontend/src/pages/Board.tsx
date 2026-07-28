import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError, nowHHMM } from "../api";
import { Button, C, LogLine, Panel, RiskBadge, Stat } from "../components";
import { t } from "../i18n";
import { useToast } from "../App";
import { TwilioHealth } from "../TwilioHealth";

type ProtoOpt = { reason: string; score: number };
type ProtoQuestion = { clip: string; options: Record<string, ProtoOpt> };
type ProtoDetail = { id: string; name_en: string; name_kn: string; questions: Record<string, ProtoQuestion> };

type Board = {
  kpis: { enrolled: number; calls_today: number; open_escalations: number; reach_rate: number };
  rows: {
    enrollment_id: string;
    patient_id?: string;
    patient_name: string;
    protocol_id: string;
    ward: string | null;
    day_index_next: number | null;
    last_call_status: string | null;
    last_risk: string | null;
    number_verified: boolean;
    open_escalation: boolean;
    outcome: string | null;
  }[];
};

type DailyStats = {
  calls_today: number;
  risk_green: number;
  risk_yellow: number;
  risk_red: number;
  calls_failed: number;
  calls_scheduled: number;
  open_escalations: number;
  resolved_today: number;
  reach_rate: number;
};

type EscRow = {
  id: string;
  patient_name: string;
  caregiver_phone: string;
  protocol_id: string;
  level: string;
  reasons: string[];
  status: string;
  created_at: string;
  acked_by: string | null;
  acked_at: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
  enrollment_id: string;
  patient_id: string | null;
  call_transcript: { node_id: string; digit: string; score: number }[];
};

type RiskWeek = { label: string; green: number; yellow: number; red: number };
type NurseRow = { username: string; display_name: string; calls_made: number; escalations_resolved: number; resolution_rate: number };

const OUTCOME_COLORS: Record<string, string> = {
  recovered: C.success,
  readmitted: C.danger,
  referred: C.warning,
  deceased: C.muted,
  lost_to_followup: C.muted,
  transferred: C.muted,
};

type WhatNowItem = {
  enrollment_id: string;
  patient_id: string;
  patient_name: string;
  day_index?: number;
  scheduled_at?: string;
  in_minutes?: number;
  hours_stale?: number;
  last_call_status?: string;
};
type WhatNow = {
  next_calls_due_2h: WhatNowItem[];
  stale_calls: WhatNowItem[];
  unresolved_red: (WhatNowItem & { escalation_id: string; status: string; age_minutes: number })[];
};

export function Board() {
  const nav = useNavigate();
  const toast = useToast();
  const [b, setB] = useState<Board | null>(null);
  const [stats, setStats] = useState<DailyStats | null>(null);
  const [escs, setEscs] = useState<EscRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [recent, setRecent] = useState<{ ts: string; line: string; tone: string }[]>([]);
  const [search, setSearch] = useState("");
  const [resolveTarget, setResolveTarget] = useState<EscRow | null>(null);
  const [resolveNote, setResolveNote] = useState("");
  const [protoCache, setProtoCache] = useState<Record<string, ProtoDetail>>({});
  const [expandedTranscript, setExpandedTranscript] = useState<string | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<any[] | null>(null);
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [selectedRow, setSelectedRow] = useState<number>(-1);
  const [riskTrend, setRiskTrend] = useState<RiskWeek[]>([]);
  const [nurseMetrics, setNurseMetrics] = useState<NurseRow[]>([]);
  const [resolveDisposition, setResolveDisposition] = useState("called_family");
  const [resolveCallbackHrs, setResolveCallbackHrs] = useState<string>("");
  const [whatNow, setWhatNow] = useState<WhatNow | null>(null);
  const [scriptedBusy, setScriptedBusy] = useState(false);
  const [scriptedResult, setScriptedResult] = useState<any | null>(null);

  async function refresh() {
    try {
      const [board, dailyStats, escalations] = await Promise.all([
        api<Board>("/api/board"),
        api<DailyStats>("/api/dashboard/daily-stats").catch(() => null),
        api<EscRow[]>("/api/escalations").catch(() => []),
      ]);
      setB(board);
      setStats(dailyStats);
      setEscs(escalations);
      setErr(null);
    } catch (ex: any) {
      setErr(ex?.message || "failed to load");
    }
  }

  async function loadExtra() {
    const [trend, nurses, wn] = await Promise.all([
      api<RiskWeek[]>("/api/dashboard/risk-trend").catch(() => []),
      api<NurseRow[]>("/api/dashboard/nurse-metrics").catch(() => []),
      api<WhatNow>("/api/board/whatnow").catch(() => null),
    ]);
    setRiskTrend(trend);
    setNurseMetrics(nurses);
    setWhatNow(wn);
  }

  useEffect(() => {
    refresh();
    loadExtra();
    const t1 = setInterval(refresh, 5000);
    const t2 = setInterval(loadExtra, 30000);
    let es: EventSource | null = null;
    try {
      es = new EventSource("/api/events");
      es.onmessage = (ev) => {
        let m: any;
        try { m = JSON.parse(ev.data); } catch { return; }
        if (m.type === "call_update" || m.type === "escalation") {
          refresh();
          setRecent((r) =>
            [{ ts: nowHHMM(), line: `live · ${m.type} · ${m.id?.slice(0, 8)}`, tone: m.type === "escalation" ? "danger" : "secondary" }, ...r].slice(0, 8),
          );
        }
      };
    } catch { /* ignore */ }
    return () => { clearInterval(t1); clearInterval(t2); es && es.close(); };
  }, []);

  // ── Keyboard shortcuts ──
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (resolveTarget) return;
      const rows = b?.rows || [];
      if (e.key === "j") { e.preventDefault(); setSelectedRow((prev) => Math.min(prev + 1, rows.length - 1)); }
      else if (e.key === "k") { e.preventDefault(); setSelectedRow((prev) => Math.max(prev - 1, 0)); }
      else if (e.key === "Enter" && selectedRow >= 0 && selectedRow < rows.length) {
        e.preventDefault();
        const row = filteredRows[selectedRow];
        if (row?.patient_id) nav(`/patients/${row.patient_id}`);
      } else if (e.key === "/") { e.preventDefault(); searchRef.current?.focus(); }
      else if (e.key === "Escape") { setSearch(""); setSelectedRow(-1); searchRef.current?.blur(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [b, selectedRow, resolveTarget, nav]);

  const filteredRows = useMemo(() => {
    if (!b) return [];
    if (!search.trim()) return b.rows;
    const q = search.toLowerCase();
    return b.rows.filter((r) =>
      r.patient_name.toLowerCase().includes(q) ||
      r.protocol_id.toLowerCase().includes(q) ||
      (r.ward && r.ward.toLowerCase().includes(q))
    );
  }, [b, search]);

  const openEscs = escs.filter((e) => e.status === "open" || e.status === "acked");
  const sosEscs = openEscs.filter((e) => e.reasons.some((r) => r.toLowerCase().includes("sos")));

  async function triggerCall(eid: string, channel: "twilio" | "sim") {
    try {
      const r = await api<{ call_id: string }>("/api/demo/trigger-call", { method: "POST", body: JSON.stringify({ enrollment_id: eid, channel }) });
      if (channel === "sim") nav(`/demo?call=${r.call_id}&eid=${eid}`);
    } catch (ex) { setErr(ex instanceof ApiError ? ex.message : "trigger failed"); }
  }

  async function runScriptedRed() {
    setScriptedBusy(true);
    setScriptedResult(null);
    try {
      const r = await api<any>("/api/demo/scripted-red", { method: "POST" });
      setScriptedResult(r);
      // Refresh the page data so the new escalation + call show up
      setTick(t => t + 1);
    } catch (ex) {
      setScriptedResult({ error: ex instanceof ApiError ? ex.message : "failed" });
    } finally {
      setScriptedBusy(false);
    }
  }

  async function resolveEscalation() {
    if (!resolveTarget) return;
    const body: any = {
      note: resolveNote || "resolved by staff",
      disposition: resolveDisposition,
    };
    const hrs = parseInt(resolveCallbackHrs, 10);
    if (Number.isFinite(hrs) && hrs > 0) {
      body.callback_in_hours = hrs;
    }
    try {
      const r = await api<{ status: string; disposition: string; callback_call_id: string | null }>(
        `/api/escalations/${resolveTarget.id}/resolve`,
        { method: "POST", body: JSON.stringify(body) },
      );
      setResolveTarget(null);
      setResolveNote("");
      setResolveDisposition("called_family");
      setResolveCallbackHrs("");
      refresh();
      const msg = r.callback_call_id
        ? `Escalation resolved — callback scheduled in ${hrs}h`
        : "Escalation resolved";
      toast.show(msg, "success");
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.message : "resolve failed");
      toast.show("Failed to resolve", "error");
    }
  }

  function exportCSV() { window.open("/api/patients/export/csv", "_blank"); toast.show("CSV download started", "success"); }

  const ensureProto = useCallback(async (pid: string) => {
    if (protoCache[pid]) return protoCache[pid];
    try { const p = await api<ProtoDetail>(`/api/protocols/${pid}/detail`); setProtoCache((c) => ({ ...c, [pid]: p })); return p; } catch { return null; }
  }, [protoCache]);

  const resolveLabel = useCallback((pid: string, nodeId: string, digit: string): string => {
    const proto = protoCache[pid]; if (!proto) return digit;
    const q = proto.questions[nodeId]; if (!q) return digit;
    const opt = q.options[digit]; return opt?.reason || digit;
  }, [protoCache]);

  useEffect(() => { openEscs.forEach((e) => ensureProto(e.protocol_id)); }, [openEscs.length]);

  async function handleImportPreview() {
    if (!importFile) return; setImportMsg(null); setImportPreview(null); setImporting(true);
    try {
      const form = new FormData(); form.append("file", importFile);
      const res = await fetch("/api/import/preview", { method: "POST", body: form, credentials: "include" });
      if (!res.ok) throw new Error(`preview failed (${res.status})`);
      const data = await res.json(); setImportPreview(data.rows || []); setImportMsg(`preview: ${(data.rows || []).length} patients found`);
    } catch (ex: any) { setImportMsg(ex.message || "preview failed"); } finally { setImporting(false); }
  }

  async function handleImportConfirm() {
    if (!importFile || !importPreview) return; setImportMsg(null); setImporting(true);
    try {
      const form = new FormData(); form.append("file", importFile);
      const res = await fetch("/api/import/confirm", { method: "POST", body: form, credentials: "include" });
      if (!res.ok) throw new Error(`import failed (${res.status})`);
      const data = await res.json(); setImportMsg(`imported ${data.imported ?? "?"} patients`); setImportPreview(null); setImportFile(null);
      if (fileRef.current) fileRef.current.value = ""; refresh(); toast.show(`Imported ${data.imported ?? "?"} patients`, "success");
    } catch (ex: any) { setImportMsg(ex.message || "import failed"); toast.show("Import failed", "error"); } finally { setImporting(false); }
  }

  // ── Loading skeleton ──
  if (err) return <Panel><LogLine tone="danger">{err}</LogLine></Panel>;
  if (!b) return (
    <Panel>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
        {[1,2,3,4].map(i => <div key={i} className="skeleton" style={{ height: 60 }} />)}
      </div>
      <div className="skeleton" style={{ height: 200, marginBottom: 12 }} />
      <div className="skeleton" style={{ height: 300 }} />
    </Panel>
  );

  const maxRisk = Math.max(1, ...riskTrend.map((w) => w.green + w.yellow + w.red));

  return (
    <div>
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 16 }}>{t("board")} · today</h2>

      {/* ── Twilio Account Health (visible to admin) ── */}
      <TwilioHealth pollMs={6000} />

      {/* ── WhatNow panel (T6) ── */}
      {whatNow && (whatNow.next_calls_due_2h.length > 0 ||
                    whatNow.stale_calls.length > 0 ||
                    whatNow.unresolved_red.length > 0) && (
        <Panel title="WHAT NOW" style={{ marginBottom: 16, borderColor: C.accent }}>
          {whatNow.next_calls_due_2h.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.08em", color: C.muted, marginBottom: 4 }}>CALLS DUE IN NEXT 2H</div>
              {whatNow.next_calls_due_2h.map((c) => (
                <div key={c.enrollment_id} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: "0.8125rem", borderTop: `1px solid ${C.borderMuted}` }}>
                  <span style={{ cursor: "pointer" }} onClick={() => c.patient_id && nav(`/patients/${c.patient_id}`)}>
                    <strong>{c.patient_name}</strong> · D{c.day_index} · in {c.in_minutes}m
                  </span>
                  <button style={btnGhost} onClick={() => triggerCall(c.enrollment_id, "sim")}>{t("sim")}</button>
                </div>
              ))}
            </div>
          )}
          {whatNow.stale_calls.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.08em", color: C.warning, marginBottom: 4 }}>STALE CALLS (overdue, retry)</div>
              {whatNow.stale_calls.map((c) => (
                <div key={c.enrollment_id} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: "0.8125rem", borderTop: `1px solid ${C.borderMuted}` }}>
                  <span style={{ cursor: "pointer" }} onClick={() => c.patient_id && nav(`/patients/${c.patient_id}`)}>
                    <strong>{c.patient_name}</strong> · {c.last_call_status} · {c.hours_stale}h stale
                  </span>
                  <button style={btnGhost} onClick={() => triggerCall(c.enrollment_id, "sim")}>{t("sim")}</button>
                </div>
              ))}
            </div>
          )}
          {whatNow.unresolved_red.length > 0 && (
            <div>
              <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.08em", color: C.danger, marginBottom: 4 }}>UNRESOLVED RED</div>
              {whatNow.unresolved_red.map((x) => (
                <div key={x.escalation_id} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: "0.8125rem", borderTop: `1px solid ${C.borderMuted}` }}>
                  <span style={{ cursor: "pointer" }} onClick={() => x.patient_id && nav(`/patients/${x.patient_id}`)}>
                    <strong>{x.patient_name}</strong> · {x.status} · {x.age_minutes}m
                  </span>
                  <button style={{ ...btnGhost, borderColor: C.danger, color: C.danger }} onClick={() => {
                    const target = escs.find((e) => e.id === x.escalation_id);
                    if (target) { setResolveTarget(target); setResolveNote(""); setResolveDisposition("called_family"); setResolveCallbackHrs(""); }
                  }}>{t("resolve")}</button>
                </div>
              ))}
            </div>
          )}
        </Panel>
      )}

      {/* ── SOS Alerts ── */}
      {sosEscs.length > 0 && (
        <Panel title={t("sos_alerts")} style={{ marginBottom: 16, borderColor: C.danger }}>
          {sosEscs.map((e) => (
            <div key={e.id} className="sos-pulse" style={{ padding: "10px 12px", marginBottom: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <span style={{ color: C.danger, fontWeight: 600 }}>[RED] {e.patient_name}</span>
                <span style={{ color: C.muted, fontSize: "0.75rem", marginLeft: 8 }}>{t("sos_detected")}</span>
                <div style={{ fontSize: "0.6875rem", color: C.muted, marginTop: 2 }}>{e.reasons.join(" · ")}</div>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                {e.patient_id && <button style={btnGhost} onClick={() => nav(`/patients/${e.patient_id}`)}>{t("view")}</button>}
                <button style={{ ...btnGhost, borderColor: C.danger, color: C.danger }} onClick={() => { setResolveTarget(e); setResolveNote(""); }}>{t("resolve")}</button>
              </div>
            </div>
          ))}
        </Panel>
      )}

      {/* ── Daily stats ── */}
      {stats && (
        <Panel title={t("daily_stats")} style={{ marginBottom: 16 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 12 }}>
            <Stat value={stats.calls_today} label={t("calls_today")} />
            <Stat value={stats.calls_scheduled} label={t("calls_scheduled")} />
            <Stat value={<span style={{ color: stats.open_escalations ? C.danger : C.text }}>{stats.open_escalations}</span>} label={t("open_esc")} />
            <Stat value={`${stats.reach_rate}%`} label={t("reach_rate")} />
          </div>
          <div style={{ display: "flex", gap: 16, fontSize: "0.8125rem" }}>
            <span style={{ color: C.success }}>● {stats.risk_green} {t("risk_green")}</span>
            <span style={{ color: C.warning }}>● {stats.risk_yellow} {t("risk_yellow")}</span>
            <span style={{ color: C.danger }}>● {stats.risk_red} {t("risk_red")}</span>
            <span style={{ color: C.muted }}>● {stats.calls_failed} {t("calls_failed")}</span>
            {stats.resolved_today > 0 && <span style={{ color: C.success }}>[OK] {stats.resolved_today} {t("resolved_today")}</span>}
          </div>
        </Panel>
      )}

      {/* ── KPIs ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
        <Stat value={b.kpis.enrolled} label={t("enrolled")} />
        <Stat value={b.kpis.calls_today} label={t("calls_today")} />
        <Stat value={<span style={{ color: b.kpis.open_escalations ? C.danger : C.text }}>{b.kpis.open_escalations}</span>} label={t("open_esc")} />
        <Stat value={`${Math.round(b.kpis.reach_rate * 100)}%`} label={t("reach_rate")} />
      </div>

      {/* ── Risk trend ── */}
      {riskTrend.length > 0 && (
        <Panel title={t("risk_trend")} style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 4, alignItems: "flex-end", height: 80 }}>
            {riskTrend.map((w, i) => {
              const scale = (w.green + w.yellow + w.red) > 0 ? 70 / maxRisk : 0;
              return (
                <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 1 }}>
                  <div style={{ width: "100%", display: "flex", flexDirection: "column-reverse", gap: 1 }}>
                    <div style={{ height: Math.max(w.green * scale, w.green > 0 ? 3 : 0), background: C.success, borderRadius: 2 }} />
                    <div style={{ height: Math.max(w.yellow * scale, w.yellow > 0 ? 3 : 0), background: C.warning, borderRadius: 2 }} />
                    <div style={{ height: Math.max(w.red * scale, w.red > 0 ? 3 : 0), background: C.danger, borderRadius: 2 }} />
                  </div>
                  <div style={{ fontSize: "0.5625rem", color: C.muted, whiteSpace: "nowrap" }}>{w.label}</div>
                </div>
              );
            })}
          </div>
          <div style={{ display: "flex", gap: 12, marginTop: 8, fontSize: "0.6875rem" }}>
            <span style={{ color: C.success }}>● green</span>
            <span style={{ color: C.warning }}>● yellow</span>
            <span style={{ color: C.danger }}>● red</span>
          </div>
        </Panel>
      )}

      {/* ── Nurse metrics ── */}
      {nurseMetrics.length > 0 && (
        <Panel title={t("nurse_metrics")} style={{ marginBottom: 16 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8125rem" }}>
            <thead>
              <tr style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase" }}>
                <Th>name</Th><Th>{t("calls_made")}</Th><Th>{t("esc_resolved")}</Th><Th>{t("resolution_rate")}</Th>
              </tr>
            </thead>
            <tbody>
              {nurseMetrics.map((n) => (
                <tr key={n.username} style={{ borderTop: `1px solid ${C.borderMuted}` }}>
                  <Td>{n.display_name}</Td><Td>{n.calls_made}</Td><Td>{n.escalations_resolved}</Td><Td>{n.resolution_rate}%</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {/* ── Open escalations ── */}
      {openEscs.length > 0 && (
        <Panel title={`${t("escalations")} · ${openEscs.length} open`} style={{ marginBottom: 16 }}>
          {openEscs.map((e) => (
            <div key={e.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0", borderTop: `1px solid ${C.borderMuted}`, gap: 12, flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 200 }}>
                <span style={{ cursor: "pointer", color: C.text }} onClick={() => e.patient_id && nav(`/patients/${e.patient_id}`)}>
                  <strong>{e.patient_name}</strong>
                </span>
                <span style={{ color: C.muted, fontSize: "0.75rem", marginLeft: 8 }}>{e.protocol_id} · {e.caregiver_phone}</span>
                <div style={{ fontSize: "0.75rem", color: C.muted, marginTop: 2 }}>{e.reasons.join(" · ")}</div>
                {e.call_transcript.length > 0 && (
                  <div style={{ marginTop: 4 }}>
                    <button style={{ ...btnGhost, fontSize: "0.6875rem", padding: "2px 6px" }} onClick={(ev) => { ev.stopPropagation(); setExpandedTranscript(expandedTranscript === e.id ? null : e.id); }}>
                      {expandedTranscript === e.id ? t("hide_transcript") : t("show_transcript")}
                    </button>
                    {expandedTranscript === e.id && (
                      <div style={{ marginTop: 4, paddingLeft: 8, borderLeft: `2px solid ${C.borderMuted}`, fontSize: "0.75rem" }}>
                        {e.call_transcript.map((r, i) => {
                          const label = resolveLabel(e.protocol_id, r.node_id, r.digit);
                          return <div key={i} style={{ padding: "2px 0", color: r.score > 0 ? (r.score >= 10 ? C.danger : C.warning) : C.muted }}>{r.node_id}: {label}{r.score > 0 && <span> (score {r.score})</span>}</div>;
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <span style={{ fontSize: "0.6875rem", padding: "2px 6px", borderRadius: 3, background: e.status === "open" ? `${C.danger}22` : `${C.warning}22`, color: e.status === "open" ? C.danger : C.warning }}>{e.status}</span>
                <span style={{ fontSize: "0.6875rem", color: C.muted }}>{new Date(e.created_at).toLocaleDateString()}</span>
                {e.patient_id && (
                  <button style={{ ...btnGhost, borderColor: C.accent, color: C.accent, fontWeight: 600 }} onClick={() => nav(`/patients/${e.patient_id}`)}>
                    View Info
                  </button>
                )}
                <button style={btnGhost} onClick={() => { setResolveTarget(e); setResolveNote(""); }}>{t("resolve")}</button>
              </div>
            </div>
          ))}
        </Panel>
      )}

      {/* ── Resolve modal ── */}
      {resolveTarget && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
          <Panel title={`resolve escalation · ${resolveTarget.patient_name}`} style={{ width: 480, maxWidth: "90vw" }}>
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.08em", color: C.muted, marginBottom: 4 }}>REASONS</div>
              {resolveTarget.reasons.map((r, i) => <LogLine key={i} tone="warning">! {r}</LogLine>)}
            </div>
            <label style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.08em" }}>DISPOSITION</label>
            <select
              value={resolveDisposition}
              onChange={(e) => setResolveDisposition(e.target.value)}
              style={{ width: "100%", padding: 8, marginTop: 4, marginBottom: 12, fontFamily: "inherit", fontSize: "0.8125rem", background: C.bg, color: C.text, border: `1px solid ${C.border}`, borderRadius: 4 }}
            >
              <option value="called_family">Called family</option>
              <option value="advised_er_visit">Advised ER visit</option>
              <option value="meds_adjusted">Meds adjusted</option>
              <option value="stable_no_action">Stable, no action</option>
              <option value="referred">Referred</option>
              <option value="callback_scheduled">Callback scheduled</option>
            </select>
            <label style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.08em" }}>CALLBACK IN (HOURS, 1-72, OPTIONAL)</label>
            <input
              type="number" min="1" max="72" placeholder="leave blank for no callback"
              value={resolveCallbackHrs}
              onChange={(e) => setResolveCallbackHrs(e.target.value)}
              style={{ width: "100%", padding: 8, marginTop: 4, marginBottom: 12, fontFamily: "inherit", fontSize: "0.8125rem", background: C.bg, color: C.text, border: `1px solid ${C.border}`, borderRadius: 4 }}
            />
            <label style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.08em" }}>{t("resolution_note")}</label>
            <textarea value={resolveNote} onChange={(e) => setResolveNote(e.target.value)} placeholder={t("resolution_note_placeholder")} style={{ width: "100%", minHeight: 60, marginTop: 4, padding: 8, fontFamily: "inherit", fontSize: "0.8125rem", background: C.bg, color: C.text, border: `1px solid ${C.border}`, borderRadius: 4, resize: "vertical" }} />
            <div style={{ display: "flex", gap: 8, marginTop: 12, justifyContent: "flex-end" }}>
              <Button variant="ghost" onClick={() => { setResolveTarget(null); setResolveNote(""); setResolveCallbackHrs(""); setResolveDisposition("called_family"); }}>{t("cancel")}</Button>
              <Button onClick={resolveEscalation}>{t("mark_resolved")}</Button>
            </div>
          </Panel>
        </div>
      )}

      {/* ── Action bar ── */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <button
          style={{ ...btnGhost, borderColor: C.danger, color: C.danger, fontWeight: 600 }}
          onClick={runScriptedRed}
          disabled={scriptedBusy}
          title="Drive a fresh sim call all the way to RED with one click — chest pain, symptoms got worse, red escalation, callback scheduled"
        >
          {scriptedBusy ? "[ running… ]" : "[ ! ] demo red scenario"}
        </button>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          <button style={{ ...btnGhost, borderColor: C.success, color: C.success }} onClick={exportCSV}>[ {t("export_csv")} ]</button>
          <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls" style={{ display: "none" }} onChange={(e) => { const f = e.target.files?.[0]; if (f) { setImportFile(f); setImportPreview(null); setImportMsg(null); } }} />
          <button style={btnGhost} onClick={() => fileRef.current?.click()}>[ {t("import_btn")} ]</button>
          {importFile && (<>
            <span style={{ fontSize: "0.75rem", color: C.muted }}>{importFile.name}</span>
            <button style={btnGhost} onClick={handleImportPreview} disabled={importing}>{importing ? "loading…" : t("preview")}</button>
          </>)}
        </div>
      </div>

      {importMsg && <div style={{ marginBottom: 12, fontSize: "0.8125rem", color: importMsg.startsWith("imported") ? C.success : C.muted }}>{importMsg}</div>}

      {scriptedResult && (
        <Panel
          title={scriptedResult.error ? "demo red scenario · FAILED" : `demo red scenario · risk=${scriptedResult.risk_level}`}
          style={{ marginBottom: 16, borderColor: scriptedResult.error ? C.danger : C.danger }}
        >
          {scriptedResult.error ? (
            <div style={{ color: C.danger, fontSize: "0.8125rem" }}>{scriptedResult.error}</div>
          ) : (
            <>
              <div style={{ fontSize: "0.8125rem", color: C.text, marginBottom: 8 }}>
                <span style={{ color: C.danger, fontWeight: 600 }}>[!] {scriptedResult.risk_level?.toUpperCase()}</span>
                {" · "}call <span style={{ fontFamily: "monospace" }}>{scriptedResult.call_id?.slice(0, 8)}</span>
                {scriptedResult.escalation_id && (
                  <>{" · "}escalation <span style={{ fontFamily: "monospace", color: C.danger }}>{scriptedResult.escalation_id?.slice(0, 8)}</span></>
                )}
              </div>
              {scriptedResult.risk_reasons && (
                <div style={{ fontSize: "0.75rem", color: C.muted, marginBottom: 8 }}>
                  reasons: {(() => { try { return JSON.parse(scriptedResult.risk_reasons).join(" · "); } catch { return scriptedResult.risk_reasons; } })()}
                </div>
              )}
              <div style={{ fontSize: "0.75rem", color: C.muted, marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                Sim transcript (driven by the engine)
              </div>
              <div style={{ maxHeight: 160, overflow: "auto", fontSize: "0.75rem", color: C.secondary, fontFamily: "monospace", padding: 6, background: C.bg, border: `1px solid ${C.borderMuted}`, borderRadius: 4 }}>
                {scriptedResult.events?.map((e: any, i: number) => (
                  <div key={i} style={{ padding: "1px 0" }}>
                    {e.type === "play" && <span><span style={{ color: C.accent }}>▸ play</span> [{e.clip}]</span>}
                    {e.type === "expect_digit" && <span><span style={{ color: C.warning }}>▸ ?</span> {e.node_id} → options: {e.options?.map((o: any) => o.digit).join(",")}</span>}
                    {e.type === "end" && <span><span style={{ color: C.muted }}>▸ end</span></span>}
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 10, fontSize: "0.75rem", color: C.muted }}>
                ↳ refresh the page in 5s to see the new escalation appear in the SOS list and Board.
              </div>
            </>
          )}
        </Panel>
      )}

      {importPreview && importPreview.length > 0 && (
        <Panel title={`import preview · ${importPreview.length} patients`} style={{ marginBottom: 16 }}>
          <div style={{ maxHeight: 200, overflow: "auto", marginBottom: 8 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.75rem" }}>
              <thead><tr style={{ color: C.muted, fontSize: "0.6875rem", textTransform: "uppercase" }}><Th>name</Th><Th>phone</Th><Th>age</Th><Th>protocol</Th><Th>ward</Th></tr></thead>
              <tbody>{importPreview.slice(0, 20).map((row: any, i: number) => (<tr key={i} style={{ borderTop: `1px solid ${C.borderMuted}` }}><Td>{row.name || "—"}</Td><Td>{row.caregiver_phone || "—"}</Td><Td>{row.age ?? "—"}</Td><Td>{row.protocol_id || "—"}</Td><Td>{row.ward || "—"}</Td></tr>))}</tbody>
            </table>
            {importPreview.length > 20 && <div style={{ color: C.muted, fontSize: "0.6875rem" }}>… and {importPreview.length - 20} more</div>}
          </div>
          <button style={{ ...btnGhost, borderColor: C.success, color: C.success }} onClick={handleImportConfirm} disabled={importing}>{t("confirm_import")}</button>
        </Panel>
      )}

      {/* ── Patient table ── */}
      <Panel title={`${t("patients")} · ${filteredRows.length}${search ? ` (${t("filtered_from")} ${b.rows.length})` : ""}`} style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 12 }}>
          <input ref={searchRef} type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("search_placeholder")} style={{ width: "100%", padding: "6px 10px", fontFamily: "inherit", fontSize: "0.8125rem", background: C.bg, color: C.text, border: `1px solid ${C.border}`, borderRadius: 4, outline: "none" }} />
        </div>
        {filteredRows.length === 0 ? (
          <div style={{ color: C.muted }}>{search ? t("no_matches") : t("no_enrollments")}</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8125rem" }}>
            <thead>
              <tr style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em" }}>
                <Th>{t("patients")}</Th><Th>{t("protocol")}</Th><Th>{t("ward")}</Th><Th>{t("next_call")}</Th><Th>{t("last_call")}</Th><Th>{t("last_risk")}</Th><Th>{t("outcome")}</Th><Th>{t("verified")}</Th><Th>{t("actions")}</Th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((r, idx) => (
                <tr key={r.enrollment_id} style={{ borderTop: `1px solid ${C.borderMuted}`, height: 44, cursor: "pointer", background: idx === selectedRow ? C.elevated : "transparent" }} onClick={() => nav(`/patients/${r.patient_id}`)}>
                  <Td>{r.patient_name}</Td>
                  <Td>{r.protocol_id}</Td>
                  <Td style={{ color: C.muted }}>{r.ward || "—"}</Td>
                  <Td>{r.day_index_next ? `D${r.day_index_next}` : "—"}</Td>
                  <Td style={{ color: C.muted }}>{r.last_call_status || "—"}</Td>
                  <Td>{r.open_escalation ? <RiskBadge level="red" /> : <RiskBadge level={r.last_risk} />}</Td>
                  <Td>{r.outcome ? <span style={{ fontSize: "0.6875rem", padding: "2px 6px", borderRadius: 3, background: `${OUTCOME_COLORS[r.outcome] || C.muted}22`, color: OUTCOME_COLORS[r.outcome] || C.muted }}>{t(r.outcome)}</span> : <span style={{ color: C.disabled }}>—</span>}</Td>
                  <Td style={{ color: r.number_verified ? C.success : C.disabled }}>{r.number_verified ? "[Y]" : "[-]"}</Td>
                  <Td onClick={(e: React.MouseEvent) => e.stopPropagation()}>
                    <button style={btnGhost} onClick={() => triggerCall(r.enrollment_id, "sim")}>{t("sim")}</button>{" "}
                    <button style={btnGhost} onClick={() => triggerCall(r.enrollment_id, "twilio")}>{t("call")}</button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title={t("recent_activity")}>
        {recent.length === 0 ? <div style={{ color: C.muted }}>—</div> : recent.map((l, i) => <LogLine key={i} tone={l.tone as any}>{l.ts} {l.line}</LogLine>)}
      </Panel>
    </div>
  );
}

const btnGhost: React.CSSProperties = { fontFamily: "inherit", fontSize: "0.75rem", background: "transparent", border: `1px solid ${C.border}`, color: C.secondary, borderRadius: 4, padding: "4px 8px", cursor: "pointer" };
const Th = ({ children }: any) => <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>{children}</th>;
const Td = ({ children, style }: any) => <td style={{ padding: "6px 8px", ...style }}>{children}</td>;
