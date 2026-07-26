import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError, nowHHMM } from "../api";
import { Button, C, Input, KeyHint, LogLine, Panel, Select } from "../components";

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
  aware_category: string;
  course_days: string;
  doses_per_day: string;
};

const EMPTY_MED: Med = {
  med_name: "",
  med_type: "other",
  aware_category: "",
  course_days: "",
  doses_per_day: "3",
};

const today = () => new Date().toISOString().slice(0, 10);

export function Intake() {
  const nav = useNavigate();
  const [protos, setProtos] = useState<Protocol[]>([]);
  const [name, setName] = useState("");
  const [age, setAge] = useState("");
  const [sex, setSex] = useState("F");
  const [abha, setAbha] = useState("");
  const [cgName, setCgName] = useState("");
  const [cgPhone, setCgPhone] = useState("+91");
  const [protoId, setProtoId] = useState("");
  const [cond, setCond] = useState("");
  const [ward, setWard] = useState("");
  const [discharge, setDischarge] = useState(today());
  const [meds, setMeds] = useState<Med[]>([{ ...EMPTY_MED, med_name: "Paracetamol 500mg" }]);
  const [consent, setConsent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ enrollment_id: string; calls: number } | null>(null);
  const [verifyLog, setVerifyLog] = useState<
    { tone: "secondary" | "danger" | "success" | "warning"; msg: string }[]
  >([]);

  useEffect(() => {
    api<Protocol[]>("/api/protocols")
      .then(setProtos)
      .catch(() => {});
  }, []);

  const phoneOk = cgPhone.startsWith("+") && cgPhone.slice(1).length >= 10 && /^\d+$/.test(cgPhone.slice(1));

  function setMed(i: number, patch: Partial<Med>) {
    setMeds((m) => m.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  }

  async function enroll() {
    setErr(null);
    setBusy(true);
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
          aware_category: m.aware_category || null,
          course_days: m.course_days ? Number(m.course_days) : null,
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
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.message : "enroll failed");
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    if (!done) return;
    setVerifyLog((l) => [
      ...l,
      { tone: "secondary", msg: `${nowHHMM()} verify-number → placing desk test call…` },
    ]);
    try {
      const r = await api<{ call_id: string }>(
        `/api/enrollments/${done.enrollment_id}/verify-number`,
        { method: "POST" },
      );
      setVerifyLog((l) => [...l, { tone: "success", msg: `${nowHHMM()} number verified ✓ (call ${r.call_id.slice(0, 8)})` }]);
    } catch (ex) {
      const msg = ex instanceof ApiError ? ex.message : "verify failed";
      const tone = ex instanceof ApiError && ex.status === 503 ? "warning" : "danger";
      setVerifyLog((l) => [...l, { tone, msg: `${nowHHMM()} ${msg}` }]);
    }
  }

  if (done) {
    return (
      <div>
        <Panel title="enrollment complete">
          <div style={{ marginBottom: 12 }}>
            <span style={{ color: C.success }}>✓ enrolled</span> · enrollment{" "}
            <code>{done.enrollment_id.slice(0, 8)}</code> · {done.calls} follow-up call(s) scheduled
          </div>
          <KeyHint>
            the family is still at the desk — place a live test call to confirm the number.
          </KeyHint>
          <div style={{ display: "flex", gap: 12, marginTop: 16, flexWrap: "wrap" }}>
            <Button variant="ghost" onClick={verify}>[ verify number ]</Button>
            <Button variant="ghost" onClick={() => nav("/board")}>[ view on board → ]</Button>
            <Button
              variant="ghost"
              onClick={() => {
                setDone(null);
                setVerifyLog([]);
                setName(""); setAge(""); setCgName(""); setCgPhone("+91"); setCond("");
                setMeds([{ ...EMPTY_MED, med_name: "Paracetamol 500mg" }]);
              }}
            >
              [ + new enrollment ]
            </Button>
          </div>
          {verifyLog.length > 0 && (
            <div style={{ marginTop: 16, borderTop: `1px solid ${C.borderMuted}`, paddingTop: 12 }}>
              {verifyLog.map((l, i) => (
                <LogLine key={i} tone={l.tone}>{l.msg}</LogLine>
              ))}
            </div>
          )}
        </Panel>
      </div>
    );
  }

  return (
    <div>
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 16 }}>intake · discharge desk</h2>

      <Panel title="patient" style={{ marginBottom: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 12 }}>
          <Input label="name" value={name} onChange={(e) => setName(e.target.value)} />
          <Input label="age" value={age} onChange={(e) => setAge(e.target.value)} />
          <Select label="sex" value={sex} onChange={(e) => setSex(e.target.value)}>
            <option value="F">F</option>
            <option value="M">M</option>
            <option value="O">O</option>
          </Select>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Input label="abha number (optional)" value={abha} onChange={(e) => setAbha(e.target.value)} />
          <Input label="ward" value={ward} onChange={(e) => setWard(e.target.value)} />
        </div>
      </Panel>

      <Panel title="caregiver & protocol" style={{ marginBottom: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Input label="caregiver name" value={cgName} onChange={(e) => setCgName(e.target.value)} />
          <Input
            label="caregiver phone (E.164)"
            value={cgPhone}
            error={cgPhone !== "+91" && !phoneOk ? "must be E.164, e.g. +91xxxxxxxxxx" : undefined}
            onChange={(e) => setCgPhone(e.target.value.trim())}
          />
        </div>
        <Input
          label="condition label"
          value={cond}
          placeholder="e.g. post-op appendectomy"
          onChange={(e) => setCond(e.target.value)}
        />
        <Input label="discharge date" type="date" value={discharge} onChange={(e) => setDischarge(e.target.value)} />
        <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em", color: C.muted, marginTop: 8, marginBottom: 8 }}>
          protocol
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

      <Panel title="medications" style={{ marginBottom: 16 }}>
        {meds.map((m, i) => (
          <div
            key={i}
            style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr auto", gap: 8, alignItems: "end" }}
          >
            <Input
              label={i === 0 ? "med name" : undefined}
              value={m.med_name}
              onChange={(e) => setMed(i, { med_name: e.target.value })}
            />
            <Select
              label={i === 0 ? "type" : undefined}
              value={m.med_type}
              onChange={(e) => setMed(i, { med_type: e.target.value })}
            >
              <option value="antibiotic">antibiotic</option>
              <option value="other">other</option>
            </Select>
            <Select
              label={i === 0 ? "AWaRe" : undefined}
              value={m.aware_category}
              onChange={(e) => setMed(i, { aware_category: e.target.value })}
            >
              <option value="">—</option>
              <option value="Access">Access</option>
              <option value="Watch">Watch</option>
              <option value="Reserve">Reserve</option>
            </Select>
            <Input
              label={i === 0 ? "course (d)" : undefined}
              value={m.course_days}
              type="number"
              min="1"
              onChange={(e) => setMed(i, { course_days: e.target.value })}
            />
            <Input
              label={i === 0 ? "doses/day" : undefined}
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
              ✕
            </Button>
            {m.med_type === "antibiotic" && (m.aware_category === "Watch" || m.aware_category === "Reserve") && (
              <span />
            )}
          </div>
        ))}
        {meds.some((m) => m.med_type === "antibiotic" && (m.aware_category === "Watch" || m.aware_category === "Reserve")) && (
          <LogLine tone="warning">
            [*] a Watch/Reserve antibiotic is prescribed — stewardship awareness nudge (J5/J7)
          </LogLine>
        )}
        <Button
          variant="ghost"
          style={{ marginTop: 12 }}
          onClick={() => setMeds((mm) => [...mm, { ...EMPTY_MED }])}
        >
          [ + add med ]
        </Button>
      </Panel>

      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <label style={{ color: C.secondary, fontSize: "0.8125rem", cursor: "pointer" }}>
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />{" "}
          family consented to follow-up calls
        </label>
        <Button
          onClick={enroll}
          disabled={busy || !consent || !name || !cgName || !phoneOk || !protoId}
        >
          [ enroll ]
        </Button>
        {!consent && (
          <span style={{ color: C.muted, fontSize: "0.8125rem" }}>
            consent required before enrollment
          </span>
        )}
      </div>

      {err && <LogLine tone="danger">{nowHHMM()} {err}</LogLine>}
    </div>
  );
}