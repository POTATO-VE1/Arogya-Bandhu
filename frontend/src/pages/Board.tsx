import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError, nowHHMM } from "../api";
import { Button, C, LogLine, Panel, RiskBadge, Stat } from "../components";

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

export function Board() {
  const nav = useNavigate();
  const [b, setB] = useState<Board | null>(null);
  const [stats, setStats] = useState<DailyStats | null>(null);
  const [escs, setEscs] = useState<EscRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [recent, setRecent] = useState<{ ts: string; line: string; tone: string }[]>([]);
  const [reminderStatus, setReminderStatus] = useState<string | null>(null);
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
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
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
      es.onerror = () => { /* polling keeps us alive */ };
    } catch { /* ignore */ }
    return () => { clearInterval(t); es && es.close(); };
  }, []);

  // client-side filter
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

  async function triggerCall(eid: string, channel: "twilio" | "sim") {
    try {
      const r = await api<{ call_id: string }>("/api/demo/trigger-call", {
        method: "POST", body: JSON.stringify({ enrollment_id: eid, channel }),
      });
      if (channel === "sim") nav(`/demo?call=${r.call_id}`);
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.message : "trigger failed");
    }
  }

  async function triggerReminders() {
    try {
      setReminderStatus("sending…");
      const r = await api<{ reminders_sent: number; pill_checks_sent: number; non_adherence_escalations: number }>(
        "/api/amr/steward/trigger", { method: "POST" },
      );
      setReminderStatus(
        `sent ${r.reminders_sent} reminder(s), ${r.pill_checks_sent} pill check(s), ${r.non_adherence_escalations} escalation(s)`
      );
      refresh();
    } catch (ex) {
      setReminderStatus(null);
      setErr(ex instanceof ApiError ? ex.message : "steward trigger failed");
    }
  }

  async function resolveEscalation() {
    if (!resolveTarget) return;
    try {
      await api(`/api/escalations/${resolveTarget.id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ note: resolveNote || "resolved by staff" }),
      });
      setResolveTarget(null);
      setResolveNote("");
      refresh();
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.message : "resolve failed");
    }
  }

  // ── protocol label resolver ──
  const ensureProto = useCallback(async (pid: string) => {
    if (protoCache[pid]) return protoCache[pid];
    try {
      const p = await api<ProtoDetail>(`/api/protocols/${pid}/detail`);
      setProtoCache((c) => ({ ...c, [pid]: p }));
      return p;
    } catch { return null; }
  }, [protoCache]);

  const resolveLabel = useCallback((pid: string, nodeId: string, digit: string): string => {
    const proto = protoCache[pid];
    if (!proto) return digit;
    const q = proto.questions[nodeId];
    if (!q) return digit;
    const opt = q.options[digit];
    return opt?.reason || digit;
  }, [protoCache]);

  // preload protocols for open escalations
  useEffect(() => {
    openEscs.forEach((e) => ensureProto(e.protocol_id));
  }, [openEscs.length]);

  // ── patient import handlers ──
  async function handleImportPreview() {
    if (!importFile) return;
    setImportMsg(null);
    setImportPreview(null);
    setImporting(true);
    try {
      const form = new FormData();
      form.append("file", importFile);
      const res = await fetch("/api/import/preview", { method: "POST", body: form, credentials: "include" });
      if (!res.ok) throw new Error(`preview failed (${res.status})`);
      const data = await res.json();
      setImportPreview(data.rows || []);
      setImportMsg(`preview: ${(data.rows || []).length} patients found`);
    } catch (ex: any) {
      setImportMsg(ex.message || "preview failed");
    } finally {
      setImporting(false);
    }
  }

  async function handleImportConfirm() {
    if (!importFile || !importPreview) return;
    setImportMsg(null);
    setImporting(true);
    try {
      const form = new FormData();
      form.append("file", importFile);
      const res = await fetch("/api/import/confirm", { method: "POST", body: form, credentials: "include" });
      if (!res.ok) throw new Error(`import failed (${res.status})`);
      const data = await res.json();
      setImportMsg(`imported ${data.imported ?? "?"} patients`);
      setImportPreview(null);
      setImportFile(null);
      if (fileRef.current) fileRef.current.value = "";
      refresh();
    } catch (ex: any) {
      setImportMsg(ex.message || "import failed");
    } finally {
      setImporting(false);
    }
  }

  if (err) return <Panel><LogLine tone="danger">{err}</LogLine></Panel>;
  if (!b) return <Panel><div style={{ color: C.muted }} className="loading-pulse">loading…</div></Panel>;

  return (
    <div>
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 16 }}>board · today</h2>

      {/* ── Daily summary stats ── */}
      {stats && (
        <Panel title="today's summary" style={{ marginBottom: 16 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 12 }}>
            <Stat value={stats.calls_today} label="calls completed" />
            <Stat value={stats.calls_scheduled} label="calls scheduled" />
            <Stat
              value={<span style={{ color: stats.open_escalations ? C.danger : C.text }}>{stats.open_escalations}</span>}
              label="open escalations"
            />
            <Stat value={`${stats.reach_rate}%`} label="reach rate" />
          </div>
          <div style={{ display: "flex", gap: 16, fontSize: "0.8125rem" }}>
            <span style={{ color: C.success }}>● {stats.risk_green} green</span>
            <span style={{ color: C.warning }}>● {stats.risk_yellow} yellow</span>
            <span style={{ color: C.danger }}>● {stats.risk_red} red</span>
            <span style={{ color: C.muted }}>● {stats.calls_failed} failed/no-answer</span>
            {stats.resolved_today > 0 && (
              <span style={{ color: C.success }}>✓ {stats.resolved_today} resolved today</span>
            )}
          </div>
        </Panel>
      )}

      {/* ── Quick KPIs (existing) ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
        <Stat value={b.kpis.enrolled} label="enrolled" />
        <Stat value={b.kpis.calls_today} label="calls today" />
        <Stat
          value={<span style={{ color: b.kpis.open_escalations ? C.danger : C.text }}>{b.kpis.open_escalations}</span>}
          label="open escalations"
        />
        <Stat value={`${Math.round(b.kpis.reach_rate * 100)}%`} label="reach rate" />
      </div>

      {/* ── Open escalations (clickable, resolvable) ── */}
      {openEscs.length > 0 && (
        <Panel title={`escalations · ${openEscs.length} open`} style={{ marginBottom: 16 }}>
          {openEscs.map((e) => (
            <div
              key={e.id}
              style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "8px 0", borderTop: `1px solid ${C.borderMuted}`,
                gap: 12, flexWrap: "wrap",
              }}
            >
              <div style={{ flex: 1, minWidth: 200 }}>
                <span
                  style={{ cursor: "pointer", color: C.text }}
                  onClick={() => e.patient_id && nav(`/patients/${e.patient_id}`)}
                >
                  <strong>{e.patient_name}</strong>
                </span>
                <span style={{ color: C.muted, fontSize: "0.75rem", marginLeft: 8 }}>
                  {e.protocol_id} · {e.caregiver_phone}
                </span>
                <div style={{ fontSize: "0.75rem", color: C.muted, marginTop: 2 }}>
                  {e.reasons.join(" · ")}
                </div>
                {e.call_transcript.length > 0 && (
                  <div style={{ marginTop: 4 }}>
                    <button
                      style={{ ...btnGhost, fontSize: "0.6875rem", padding: "2px 6px" }}
                      onClick={(ev) => { ev.stopPropagation(); setExpandedTranscript(expandedTranscript === e.id ? null : e.id); }}
                    >
                      {expandedTranscript === e.id ? "▾ hide transcript" : "▸ show transcript"}
                    </button>
                    {expandedTranscript === e.id && (
                      <div style={{ marginTop: 4, paddingLeft: 8, borderLeft: `2px solid ${C.borderMuted}`, fontSize: "0.75rem" }}>
                        {e.call_transcript.map((r, i) => {
                          const label = resolveLabel(e.protocol_id, r.node_id, r.digit);
                          return (
                            <div key={i} style={{ padding: "2px 0", color: r.score > 0 ? (r.score >= 10 ? C.danger : C.warning) : C.muted }}>
                              {r.node_id}: {label}
                              {r.score > 0 && <span> (score {r.score})</span>}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <span style={{
                  fontSize: "0.6875rem", padding: "2px 6px", borderRadius: 3,
                  background: e.status === "open" ? `${C.danger}22` : `${C.warning}22`,
                  color: e.status === "open" ? C.danger : C.warning,
                }}>
                  {e.status}
                </span>
                <span style={{ fontSize: "0.6875rem", color: C.muted }}>
                  {new Date(e.created_at).toLocaleDateString()}
                </span>
                <button
                  style={btnGhost}
                  onClick={() => { setResolveTarget(e); setResolveNote(""); }}
                >
                  [ resolve ]
                </button>
              </div>
            </div>
          ))}
        </Panel>
      )}

      {/* ── Resolve modal ── */}
      {resolveTarget && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
          display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
        }}>
          <Panel title={`resolve escalation · ${resolveTarget.patient_name}`} style={{ width: 420, maxWidth: "90vw" }}>
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: "0.8125rem", color: C.muted, marginBottom: 4 }}>reasons:</div>
              {resolveTarget.reasons.map((r, i) => (
                <LogLine key={i} tone="warning">⚠ {r}</LogLine>
              ))}
            </div>
            <label style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.1em" }}>
              resolution note
            </label>
            <textarea
              value={resolveNote}
              onChange={(e) => setResolveNote(e.target.value)}
              placeholder="e.g. called family, adjusted medication, patient stable"
              style={{
                width: "100%", minHeight: 60, marginTop: 4, padding: 8,
                fontFamily: "inherit", fontSize: "0.8125rem",
                background: C.bg, color: C.text, border: `1px solid ${C.border}`, borderRadius: 4,
                resize: "vertical",
              }}
            />
            <div style={{ display: "flex", gap: 8, marginTop: 12, justifyContent: "flex-end" }}>
              <Button variant="ghost" onClick={() => setResolveTarget(null)}>[ cancel ]</Button>
              <Button onClick={resolveEscalation}>[ mark resolved ]</Button>
            </div>
          </Panel>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <button style={{ ...btnGhost, borderColor: C.accent, color: C.accent }} onClick={triggerReminders}>
          [ send reminders ]
        </button>
        {reminderStatus && <span style={{ color: C.muted, fontSize: "0.75rem" }}>{reminderStatus}</span>}
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls" style={{ display: "none" }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) { setImportFile(f); setImportPreview(null); setImportMsg(null); } }}
          />
          <button style={btnGhost} onClick={() => fileRef.current?.click()}>
            [ import csv ]
          </button>
          {importFile && (
            <>
              <span style={{ fontSize: "0.75rem", color: C.muted }}>{importFile.name}</span>
              <button style={btnGhost} onClick={handleImportPreview} disabled={importing}>
                {importing ? "loading…" : "[ preview ]"}
              </button>
            </>
          )}
        </div>
      </div>

      {importMsg && (
        <div style={{ marginBottom: 12, fontSize: "0.8125rem", color: importMsg.startsWith("imported") ? C.success : C.muted }}>
          {importMsg}
        </div>
      )}

      {importPreview && importPreview.length > 0 && (
        <Panel title={`import preview · ${importPreview.length} patients`} style={{ marginBottom: 16 }}>
          <div style={{ maxHeight: 200, overflow: "auto", marginBottom: 8 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.75rem" }}>
              <thead>
                <tr style={{ color: C.muted, fontSize: "0.6875rem", textTransform: "uppercase" }}>
                  <Th>name</Th><Th>phone</Th><Th>age</Th><Th>protocol</Th><Th>ward</Th>
                </tr>
              </thead>
              <tbody>
                {importPreview.slice(0, 20).map((row: any, i: number) => (
                  <tr key={i} style={{ borderTop: `1px solid ${C.borderMuted}` }}>
                    <Td>{row.name || "—"}</Td><Td>{row.caregiver_phone || "—"}</Td>
                    <Td>{row.age ?? "—"}</Td><Td>{row.protocol_id || "—"}</Td><Td>{row.ward || "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
            {importPreview.length > 20 && <div style={{ color: C.muted, fontSize: "0.6875rem" }}>… and {importPreview.length - 20} more</div>}
          </div>
          <button style={{ ...btnGhost, borderColor: C.success, color: C.success }} onClick={handleImportConfirm} disabled={importing}>
            [ confirm import ]
          </button>
        </Panel>
      )}

      {/* ── Patient search + enrollment table ── */}
      <Panel
        title={`enrollments · ${filteredRows.length}${search ? ` (filtered from ${b.rows.length})` : ""}`}
        style={{ marginBottom: 16 }}
      >
        <div style={{ marginBottom: 12 }}>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="search by name, protocol, or ward…"
            style={{
              width: "100%", padding: "6px 10px",
              fontFamily: "inherit", fontSize: "0.8125rem",
              background: C.bg, color: C.text,
              border: `1px solid ${C.border}`, borderRadius: 4,
              outline: "none",
            }}
          />
        </div>
        {filteredRows.length === 0 ? (
          <div style={{ color: C.muted }}>{search ? "no matches" : "no enrollments — [ + new ] on the intake page"}</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8125rem" }}>
            <thead>
              <tr style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em" }}>
                <Th>patient</Th><Th>protocol</Th><Th>ward</Th><Th>next</Th><Th>last call</Th><Th>risk</Th><Th>verified</Th><Th>actions</Th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((r) => (
                <tr
                  key={r.enrollment_id}
                  style={{ borderTop: `1px solid ${C.borderMuted}`, height: 44, cursor: "pointer" }}
                  onClick={() => nav(`/patients/${r.patient_id}`)}
                >
                  <Td>{r.patient_name}</Td>
                  <Td>{r.protocol_id}</Td>
                  <Td style={{ color: C.muted }}>{r.ward || "—"}</Td>
                  <Td>{r.day_index_next ? `D${r.day_index_next}` : "—"}</Td>
                  <Td style={{ color: C.muted }}>{r.last_call_status || "—"}</Td>
                  <Td>{r.open_escalation ? <span style={{ color: C.danger, fontWeight: 600 }}>[RED]</span> : <RiskBadge level={r.last_risk} />}</Td>
                  <Td style={{ color: r.number_verified ? C.success : C.disabled }}>{r.number_verified ? "✓" : "—"}</Td>
                  <Td onClick={(e: React.MouseEvent) => e.stopPropagation()}>
                    <button style={btnGhost} onClick={() => triggerCall(r.enrollment_id, "sim")}>[ sim ]</button>{" "}
                    <button style={btnGhost} onClick={() => triggerCall(r.enrollment_id, "twilio")}>[ call ]</button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="recent activity">
        {recent.length === 0 ? (
          <div style={{ color: C.muted }}>—</div>
        ) : (
          recent.map((l, i) => (
            <LogLine key={i} tone={l.tone as any}>{l.ts} {l.line}</LogLine>
          ))
        )}
      </Panel>
    </div>
  );
}

const btnGhost: React.CSSProperties = {
  fontFamily: "inherit",
  fontSize: "0.75rem",
  background: "transparent",
  border: `1px solid ${C.border}`,
  color: C.secondary,
  borderRadius: 4,
  padding: "4px 8px",
  cursor: "pointer",
};

const Th = ({ children }: any) => (
  <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>{children}</th>
);
const Td = ({ children, style }: any) => (
  <td style={{ padding: "6px 8px", ...style }}>{children}</td>
);
