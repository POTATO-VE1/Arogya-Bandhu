import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError, nowHHMM } from "../api";
import { useAuth } from "../App";
import { Button, C, Input, KeyHint, LogLine, Panel, Select } from "../components";
import { t } from "../i18n";

type Protocol = {
  id: string;
  name_en: string;
  name_kn: string;
  condition: string;
  schedule_days: number[];
};
type Med = {
  med_name: string;
  med_type: string;
  doses_per_day: string;
};

const EMPTY_MED: Med = {
  med_name: "",
  med_type: "other",
  doses_per_day: "3",
};

type ReportFile = { name: string; size: number; file: File };

// L1: IST-aware date for the discharge default. Falls back to a
// local computation (UTC + 5:30) so the front-end works even if the
// backend helper isn't reachable.
const todayIst = () => {
  const ms = Date.now() + 5.5 * 60 * 60 * 1000;
  return new Date(ms).toISOString().slice(0, 10);
};

export function Intake() {
  const nav = useNavigate();
  const { user } = useAuth();
  const [protos, setProtos] = useState<Protocol[]>([]);
  const [wards, setWards] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [age, setAge] = useState("");
  const [sex, setSex] = useState("F");
  const [abha, setAbha] = useState("");
  const [cgName, setCgName] = useState("");
  const [cgPhone, setCgPhone] = useState("+91");
  const [protoId, setProtoId] = useState("");
  const [cond, setCond] = useState("");
  // L1: ward pre-fills from the logged-in nurse/staff's assigned ward.
  // If user has a ward (nurse/staff), it's locked. If admin/doctor,
  // they can pick from the existing wards list.
  const initialWard = user?.ward || "";
  const [ward, setWard] = useState(initialWard);
  const wardLocked = Boolean(user?.ward);
  const [discharge, setDischarge] = useState(todayIst());
  const [meds, setMeds] = useState<Med[]>([{ ...EMPTY_MED, med_name: "Paracetamol 500mg" }]);
  const [reports, setReports] = useState<ReportFile[]>([]);
  const [consent, setConsent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ enrollment_id: string; calls: number } | null>(null);
  const [verifyLog, setVerifyLog] = useState<
    { tone: "secondary" | "danger" | "success" | "warning"; msg: string }[]
  >([]);
  const [uploadLog, setUploadLog] = useState<
    { tone: "secondary" | "danger" | "success" | "warning"; msg: string }[]
  >([]);
  // T11: pending upload — files attached after a successful enroll but the
  // user hasn't confirmed yet. Survives a network drop on the upload step.
  const [pendingUploads, setPendingUploads] = useState<ReportFile[]>([]);
  const [pendingUploadEnrollId, setPendingUploadEnrollId] = useState<string | null>(null);
  const [uploadPrompt, setUploadPrompt] = useState<"ask" | "uploading" | null>(null);

  useEffect(() => {
    api<Protocol[]>("/api/protocols").then(setProtos).catch(() => {});
    // L3 helper: fetch the list of distinct wards for the admin/doctor
    // selector. Nurse/staff skip this — their ward is locked.
    if (!user?.ward) {
      api<string[]>("/api/analytics/wards").then(setWards).catch(() => {});
    }
  }, [user?.ward]);

  const phoneOk = cgPhone.startsWith("+") && cgPhone.slice(1).length >= 10 && /^\d+$/.test(cgPhone.slice(1));

  function setMed(i: number, patch: Partial<Med>) {
    setMeds((m) => m.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  }

  async function uploadReports(eid: string, files: ReportFile[]) {
    if (files.length === 0) return;
    setUploadLog([{ tone: "secondary", msg: `${nowHHMM()} uploading ${files.length} report(s)…` }]);
    const next: { tone: "secondary" | "danger" | "success" | "warning"; msg: string }[] = [];
    for (const r of files) {
      try {
        const fd = new FormData();
        fd.append("files", r.file, r.name);
        const resp = await fetch(`/api/enrollments/${eid}/reports`, {
          method: "POST",
          body: fd,
          credentials: "include",
        });
        if (!resp.ok) {
          const detail = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
          next.push({ tone: "danger", msg: `${nowHHMM()} ${r.name}: ${detail.detail || `HTTP ${resp.status}`}` });
        } else {
          const data = await resp.json();
          const count = (data.uploaded || []).length;
          next.push({ tone: "success", msg: `${nowHHMM()} ${r.name}: uploaded (${count} file${count === 1 ? "" : "s"})` });
        }
      } catch (ex: any) {
        next.push({ tone: "danger", msg: `${nowHHMM()} ${r.name}: ${ex?.message || "upload failed"}` });
      }
    }
    setUploadLog(next);
  }

  async function enroll() {
    setErr(null);
    setBusy(true);
    setUploadLog([]);
    const body = {
      patient: {
        name, age: age ? Number(age) : null, sex, abha_number: abha || null,
        caregiver_name: cgName, caregiver_phone: cgPhone,
      },
      protocol_id: protoId,
      condition_label: cond,
      ward: ward || null,
      discharge_date: discharge,
      meds: meds
        .filter((m) => m.med_name.trim())
        .map((m) => ({
          med_name: m.med_name,
          med_type: m.med_type,
          doses_per_day: Number(m.doses_per_day) || 3,
        })),
      consent,
    };
    try {
      const r = await api<{ enrollment_id: string; patient_id: string; call_ids: string[] }>(
        "/api/enrollments",
        { method: "POST", body: JSON.stringify(body) },
      );
      setDone({ enrollment_id: r.enrollment_id, calls: r.call_ids.length });
      if (reports.length > 0) {
        // T11: pause and ask the nurse whether to upload now, later, or skip.
        setPendingUploads(reports);
        setPendingUploadEnrollId(r.enrollment_id);
        setUploadPrompt("ask");
      }
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.message : "enroll failed");
    } finally {
      setBusy(false);
    }
  }

  async function doUploadNow() {
    if (pendingUploads.length === 0 || !pendingUploadEnrollId) return;
    setUploadPrompt("uploading");
    // Snapshot the current pendingUploads — uploadReports resets state.
    const files = pendingUploads;
    try {
      await uploadReports(pendingUploadEnrollId, files);
    } catch (ex) {
      setUploadLog((l) => [...l, { tone: "danger", msg: `${nowHHMM()} upload error: ${(ex as any)?.message || "unknown"}` }]);
    } finally {
      setUploadPrompt(null);
      setPendingUploads([]);
      setPendingUploadEnrollId(null);
    }
  }

  function deferUpload() {
    // Keep pendingUploads + pendingUploadEnrollId in state; the user can
    // retry later from the badge. The prompt closes, but the data sticks.
    setUploadPrompt(null);
    setUploadLog((l) => [...l, { tone: "warning", msg: `${nowHHMM()} ${pendingUploads.length} report(s) deferred — retry from the badge below the form` }]);
  }

  async function verify() {
    if (!done) return;
    setVerifyLog((l) => [
      ...l,
      { tone: "secondary", msg: `${nowHHMM()} verify-number → marking verified at desk` },
    ]);
    try {
      await api<{ call_id: string | null; verified: boolean; method: string }>(
        `/api/enrollments/${done.enrollment_id}/verify-number`,
        { method: "POST", body: JSON.stringify({ method: "desk", confirmed: true }) },
      );
      setVerifyLog((l) => [...l, { tone: "success", msg: `${nowHHMM()} number verified at desk` }]);
    } catch (ex) {
      const msg = ex instanceof ApiError ? ex.message : "verify failed";
      const tone = ex instanceof ApiError && ex.status === 503 ? "warning" : "danger";
      setVerifyLog((l) => [...l, { tone, msg: `${nowHHMM()} ${msg}` }]);
    }
  }

  async function verifyVoice() {
    if (!done) return;
    setVerifyLog((l) => [
      ...l,
      { tone: "secondary", msg: `${nowHHMM()} verify-number → placing voice test call…` },
    ]);
    try {
      const r = await api<{ call_id: string | null; verified: boolean; method: string }>(
        `/api/enrollments/${done.enrollment_id}/verify-number`,
        { method: "POST", body: JSON.stringify({ method: "voice" }) },
      );
      const cid = r.call_id ? r.call_id.slice(0, 8) : "—";
      setVerifyLog((l) => [...l, { tone: "success", msg: `${nowHHMM()} voice test call placed (call ${cid})` }]);
    } catch (ex) {
      const msg = ex instanceof ApiError ? ex.message : "verify failed";
      const tone = ex instanceof ApiError && ex.status === 503 ? "warning" : "danger";
      setVerifyLog((l) => [...l, { tone, msg: `${nowHHMM()} ${msg}` }]);
    }
  }

  if (done) {
    return (
      <div>
        <Panel title={t("enrollment_complete")}>
          <div style={{ marginBottom: 12 }}>
            <span style={{ color: C.success }}>{t("enrolled_check")}</span> · {t("enrollment_id")}{" "}
            <code>{done.enrollment_id.slice(0, 8)}</code> · {done.calls} {t("followup_scheduled")}
          </div>
          <KeyHint>
            {t("verify_hint")}
          </KeyHint>
          <div style={{ display: "flex", gap: 12, marginTop: 16, flexWrap: "wrap" }}>
            <Button variant="ghost" onClick={verify}>{t("verify_number")}</Button>
            <Button variant="ghost" onClick={verifyVoice}>{t("verify_voice")}</Button>
            <Button variant="ghost" onClick={() => nav("/board")}>{t("view_board")}</Button>
            <Button
              variant="ghost"
              onClick={() => {
                setDone(null);
                setVerifyLog([]);
                setUploadLog([]);
                setReports([]);
                setName(""); setAge(""); setCgName(""); setCgPhone("+91"); setCond("");
                setMeds([{ ...EMPTY_MED, med_name: "Paracetamol 500mg" }]);
              }}
            >
              {t("new_enrollment")}
            </Button>
          </div>
          {verifyLog.length > 0 && (
            <div style={{ marginTop: 16, borderTop: `1px solid ${C.borderMuted}`, paddingTop: 12 }}>
              {verifyLog.map((l, i) => (
                <LogLine key={i} tone={l.tone}>{l.msg}</LogLine>
              ))}
            </div>
          )}
          {uploadLog.length > 0 && (
            <div style={{ marginTop: 16, borderTop: `1px solid ${C.borderMuted}`, paddingTop: 12 }}>
              <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.08em", color: C.muted, marginBottom: 6 }}>REPORTS</div>
              {uploadLog.map((l, i) => (
                <LogLine key={i} tone={l.tone}>{l.msg}</LogLine>
              ))}
            </div>
          )}
        </Panel>

        {/* T11: pause-before-upload modal */}
        {uploadPrompt === "ask" && (
          <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
            <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: 24, width: 420, maxWidth: "92vw" }}>
              <div style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>Upload reports?</div>
              <div style={{ fontSize: "0.875rem", color: C.text, marginBottom: 12 }}>
                Enrollment saved ({pendingUploads.length} report{pendingUploads.length === 1 ? "" : "s"} attached).
                Upload now, defer to later, or skip?
              </div>
              <div style={{ fontSize: "0.75rem", color: C.muted, marginBottom: 12 }}>
                Files: {pendingUploads.map((f) => f.name).join(", ")}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <Button variant="ghost" style={{ flex: 1 }} onClick={() => { setUploadPrompt(null); setPendingUploads([]); setPendingUploadEnrollId(null); }}>
                  [ skip ]
                </Button>
                <Button variant="ghost" style={{ flex: 1 }} onClick={deferUpload}>
                  [ defer ]
                </Button>
                <Button style={{ flex: 1 }} onClick={doUploadNow}>
                  [ upload now ]
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div>
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 16 }}>{t("intake_heading")}</h2>

      {/* T11: deferred-upload badge — files the nurse deferred last enrollment */}
      {pendingUploads.length > 0 && pendingUploadEnrollId && uploadPrompt === null && done === null && (
        <div style={{ marginBottom: 16, padding: 10, background: C.elevated, border: `1px solid ${C.warning}`, borderRadius: 4, fontSize: "0.8125rem", color: C.warning, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>! {pendingUploads.length} unsynced report{pendingUploads.length === 1 ? "" : "s"} from previous enrollment (id {pendingUploadEnrollId.slice(0, 8)})</span>
          <div style={{ display: "flex", gap: 8 }}>
            <Button variant="ghost" style={{ fontSize: "0.75rem", padding: "4px 8px" }} onClick={() => {
              setPendingUploads([]);
              setPendingUploadEnrollId(null);
            }}>
              [ discard ]
            </Button>
            <Button style={{ fontSize: "0.75rem", padding: "4px 8px" }} onClick={() => setUploadPrompt("ask")}>
              [ retry ]
            </Button>
          </div>
        </div>
      )}

      <Panel title={t("patient")} style={{ marginBottom: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 12 }}>
          <Input label={t("name")} value={name} onChange={(e) => setName(e.target.value)} />
          <Input label={t("age")} value={age} onChange={(e) => setAge(e.target.value)} />
          <Select label={t("sex")} value={sex} onChange={(e) => setSex(e.target.value)}>
            <option value="F">F</option>
            <option value="M">M</option>
            <option value="O">O</option>
          </Select>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Input label={t("abha_optional")} value={abha} onChange={(e) => setAbha(e.target.value)} />
          {wardLocked ? (
            <Input
              label={`${t("ward")} (assigned)`}
              value={ward}
              readOnly
              onChange={() => {}}
            />
          ) : (
            <Select
              label={t("ward")}
              value={ward}
              onChange={(e) => setWard(e.target.value)}
            >
              <option value="">-- select ward --</option>
              {wards.map((w) => (
                <option key={w} value={w}>{w}</option>
              ))}
            </Select>
          )}
        </div>
      </Panel>

      {/* ── Reports / discharge summary upload ── */}
      <Panel title={t("reports_discharge")} style={{ marginBottom: 16 }}>
        <div style={{ fontSize: "0.8125rem", color: C.muted, marginBottom: 8 }}>
          {t("upload_hint")}
        </div>
        <label style={{ display: "inline-block", padding: "8px 14px", border: `1px dashed ${C.border}`, borderRadius: 4, color: C.muted, fontSize: "0.8125rem", cursor: "pointer" }}>
          <input
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,.doc,.docx"
            multiple
            style={{ display: "none" }}
            onChange={(e) => {
              const files = Array.from(e.target.files || []);
              const valid = files.filter((f) => f.size <= 10 * 1024 * 1024);
              setReports((prev) => [...prev, ...valid.map((f) => ({ name: f.name, size: f.size, file: f }))]);
            }}
          />
          {t("add_report")}
        </label>
        {reports.length > 0 && (
          <div style={{ marginTop: 12 }}>
            {reports.map((r, i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderTop: `1px solid ${C.borderMuted}`, fontSize: "0.8125rem" }}>
                <span>
                  <span style={{ color: C.text }}>{r.name}</span>
                  <span style={{ color: C.muted, marginLeft: 8 }}>({(r.size / 1024).toFixed(0)}KB)</span>
                </span>
                <button style={{ ...btnGhost, fontSize: "0.6875rem", padding: "2px 6px" }} onClick={() => setReports((prev) => prev.filter((_, j) => j !== i))}>[ x ]</button>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title={t("caregiver_protocol")} style={{ marginBottom: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Input label={t("caregiver_name")} value={cgName} onChange={(e) => setCgName(e.target.value)} />
          <Input
            label={t("caregiver_phone")}
            value={cgPhone}
            error={cgPhone !== "+91" && !phoneOk ? t("phone_error") : undefined}
            onChange={(e) => setCgPhone(e.target.value.trim())}
          />
        </div>
        <Input
          label={t("condition_label")}
          value={cond}
          placeholder={t("condition_placeholder")}
          onChange={(e) => setCond(e.target.value)}
        />
        <Input label={t("discharge_date")} type="date" value={discharge} onChange={(e) => setDischarge(e.target.value)} />
        <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em", color: C.muted, marginTop: 8, marginBottom: 8 }}>
          {t("protocol")}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {protos.map((p) => (
            <button
              key={p.id}
              onClick={() => setProtoId(p.id)}
              style={{
                textAlign: "left",
                padding: "12px 14px",
                borderRadius: 4,
                cursor: "pointer",
                fontFamily: "inherit",
                border: `1px solid ${protoId === p.id ? C.accent : C.border}`,
                background: protoId === p.id ? C.elevated : "transparent",
                color: C.text,
              }}
            >
              <div style={{ fontWeight: 600 }}>[*] {p.name_en}</div>
              <div style={{ color: C.muted, fontSize: "0.8125rem", fontFamily: "var(--color-kn)" }}>
                {p.name_kn} · schedule D{p.schedule_days.join("/D")}
              </div>
            </button>
          ))}
        </div>
      </Panel>

      <Panel title={t("medications")} style={{ marginBottom: 16 }}>
        {meds.map((m, i) => (
          <div
            key={i}
            style={{ display: "grid", gridTemplateColumns: "3fr 1fr 1fr auto", gap: 8, alignItems: "end" }}
          >
            <Input
              label={i === 0 ? t("med_name") : undefined}
              value={m.med_name}
              onChange={(e) => setMed(i, { med_name: e.target.value })}
            />
            <Select
              label={i === 0 ? t("type") : undefined}
              value={m.med_type}
              onChange={(e) => setMed(i, { med_type: e.target.value })}
            >
              <option value="antibiotic">antibiotic</option>
              <option value="other">other</option>
            </Select>
            <Input
              label={i === 0 ? t("doses_day") : undefined}
              value={m.doses_per_day}
              type="number"
              min="1"
              onChange={(e) => setMed(i, { doses_per_day: e.target.value })}
            />
            <Button
              variant="ghost"
              style={{ padding: "10px 10px" }}
              onClick={() => setMeds((mm) => mm.filter((_, j) => j !== i))}
            >
              [ x ]
            </Button>
          </div>
        ))}
        <Button
          variant="ghost"
          style={{ marginTop: 12 }}
          onClick={() => setMeds((mm) => [...mm, { ...EMPTY_MED }])}
        >
          {t("add_med")}
        </Button>
      </Panel>

      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <label style={{ color: C.secondary, fontSize: "0.8125rem", cursor: "pointer" }}>
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />{" "}
          {t("consent_text")}
        </label>
        <Button
          onClick={enroll}
          disabled={busy || !consent || !name || !cgName || !phoneOk || !protoId}
        >
          {t("enroll_btn")}
        </Button>
        {!consent && (
          <span style={{ color: C.muted, fontSize: "0.8125rem" }}>
            {t("consent_required")}
          </span>
        )}
      </div>

      {err && <LogLine tone="danger">{nowHHMM()} {err}</LogLine>}
    </div>
  );
}

const btnGhost: React.CSSProperties = { fontFamily: "inherit", fontSize: "0.75rem", background: "transparent", border: `1px solid ${C.border}`, color: C.secondary, borderRadius: 4, padding: "4px 8px", cursor: "pointer" };