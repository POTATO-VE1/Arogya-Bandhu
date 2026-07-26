import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { api, ApiError } from "../api";
import { C, LogLine } from "../components";

type Sheet = {
  hospital_name: string;
  patient_name: string;
  age: number | null;
  sex: string | null;
  condition_label: string;
  discharge_date: string;
  bullets_kn: string[];
  sheet_source: string;
  schedule_days: number[];
  meds: { name: string; aware: string | null; course_days: number | null; doses_per_day: number }[];
  telephones: string;
};

export function Sheet() {
  const { eid } = useParams<{ eid: string }>();
  const [s, setS] = useState<Sheet | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<Sheet>(`/api/enrollments/${eid}/sheet`)
      .then(setS)
      .catch((ex) => setErr(ex instanceof ApiError ? (ex.status === 404 ? "not found" : ex.message) : "failed"));
  }, [eid]);

  if (err) return <div style={{ padding: 24, color: C.danger }}><LogLine tone="danger">{err}</LogLine></div>;
  if (!s) return <div style={{ padding: 24, color: C.muted }}>…</div>;

  return (
    <>
      <div style={{ padding: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }} className="no-print">
        <span style={{ color: C.muted, fontSize: "0.75rem" }}>kannada caregiver sheet · {s.sheet_source}</span>
        <button onClick={() => window.print()} style={{ fontFamily: "inherit", background: "transparent", border: `1px solid ${C.border}`, color: C.secondary, borderRadius: 4, padding: "6px 10px" }}>
          [ print ]
        </button>
      </div>

      <div className="sheet" style={{
        background: "#fff", color: "#000", fontFamily: "var(--color-kn)",
        maxWidth: 720, margin: "0 auto", padding: 32, minHeight: "80vh",
      }}>
        <div style={{ textAlign: "center", borderBottom: "1px solid #000", paddingBottom: 8, marginBottom: 12 }}>
          <strong style={{ fontSize: "1.1rem" }}>{s.hospital_name}</strong>
          <div style={{ fontSize: "0.85rem" }}>ಡಿಸ್ಚಾರ್ಜ್ ನಂತರದ ಕಾಳಜಿ ಸೂಚನೆಗಳು</div>
        </div>

        <table style={{ width: "100%", fontSize: "0.9rem", marginBottom: 12 }}>
          <tbody>
            <tr><td style={{ width: "40%" }}><strong>ರೋಗಿಯ ಹೆಸರು:</strong></td><td>{s.patient_name}</td></tr>
            <tr><td><strong>ವಯಸ್ಸು / ಲಿಂಗ:</strong></td><td>{s.age ?? "?"} / {s.sex ?? "?"}</td></tr>
            <tr><td><strong>ಕಾಯಿಲೆ:</strong></td><td>{s.condition_label}</td></tr>
            <tr><td><strong>ಡಿಸ್ಚಾರ್ಜ್ ದಿನಾಂಕ:</strong></td><td>{s.discharge_date}</td></tr>
          </tbody>
        </table>

        <div style={{ fontWeight: 700, marginBottom: 6 }}>ಮುಖ್ಯ ಸೂಚನೆಗಳು</div>
        <ul style={{ margin: 0, paddingLeft: 20, lineHeight: 1.6 }}>
          {s.bullets_kn.map((b, i) => <li key={i} style={{ fontSize: "0.95rem" }}>{b}</li>)}
        </ul>

        <div style={{ fontWeight: 700, marginTop: 14, marginBottom: 6 }}>ಔಷಧಿಗಳು</div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid #000" }}>
              <th style={{ textAlign: "left", padding: 4 }}>ಔಷಧಿ</th>
              <th style={{ textAlign: "left", padding: 4 }}>ದಿನಕ್ಕೆ</th>
              <th style={{ textAlign: "left", padding: 4 }}>ದಿನಗಳು</th>
              <th style={{ textAlign: "left", padding: 4 }}>AWaRe</th>
            </tr>
          </thead>
          <tbody>
            {s.meds.map((m, i) => (
              <tr key={i} style={{ borderBottom: "1px solid #ccc" }}>
                <td style={{ padding: 4 }}>{m.name}</td>
                <td style={{ padding: 4 }}>{m.doses_per_day}</td>
                <td style={{ padding: 4 }}>{m.course_days ? `${m.course_days} ದಿನ` : "—"}</td>
                <td style={{ padding: 4 }}>{m.aware || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <div style={{ fontWeight: 700, marginTop: 14, marginBottom: 6 }}>ಫೋಲೋ-ಅಪ್ ಕರೆ ದಿನಗಳು</div>
        <div style={{ fontSize: "0.95rem" }}>
          ಡಿಸ್ಚಾರ್ಜ್ ಆದ ದಿನದಿಂದ {s.schedule_days.map((d) => `${d}ನೇ ದಿನ`).join(" · ")} ಕರೆ ಬರುತ್ತದೆ.
        </div>

        <div style={{
          border: "2px solid #c00", marginTop: 16, padding: 10, fontWeight: 700,
        }}>
          ⚠ ಈ ಲಕ್ಷಣಗಳು ಕಂಡರೆ ತಕ್ಷಣ ಆಸ್ಪತ್ರೆಗೆ ಬನ್ನಿ:
          <div style={{ fontWeight: 400, marginTop: 4 }}>
            ಹುಳು, ರಕ್ತಸ್ರಾವ, ಗಂಭೀರ ಜ್ವರ, ಉಸಿರಾಟದ ತೊಂದರೆ
          </div>
          <div style={{ marginTop: 6, fontWeight: 700 }}>
            ತುರ್ತು ಸಹಾಯಕ್ಕೆ → {s.telephones}
          </div>
        </div>

        <div style={{ marginTop: 20, fontSize: "0.7rem", color: "#444", borderTop: "1px solid #999", paddingTop: 8 }}>
          consent: family consented to follow-up calls · triage layer — not an emergency service · Aarogya Bandhu
        </div>
      </div>
    </>
  );
}