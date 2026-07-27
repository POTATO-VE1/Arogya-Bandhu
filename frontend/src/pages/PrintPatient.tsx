import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { api, ApiError } from "../api";
import { C } from "../components";
import { t } from "../i18n";

type Enroll = {
  id: string;
  protocol_id: string;
  condition_label: string;
  ward: string | null;
  status: string;
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

export function PrintPatient() {
  const { id } = useParams<{ id: string }>();
  const [p, setP] = useState<Patient | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api<Patient>(`/api/patients/${id}`)
      .then(setP)
      .catch((ex) => setErr(ex instanceof ApiError ? "patient not found" : "failed"));
  }, [id]);

  useEffect(() => {
    if (p) {
      // auto-print after render
      const t = setTimeout(() => window.print(), 300);
      return () => clearTimeout(t);
    }
  }, [p]);

  if (err) return <div style={{ padding: 40, fontFamily: "monospace" }}>{err}</div>;
  if (!p) return <div style={{ padding: 40, fontFamily: "monospace" }}>{t("loading")}</div>;

  const en = p.enrollments[0];
  const sortedCalls = en ? [...en.calls].sort((a, b) => a.day_index - b.day_index) : [];

  return (
    <div style={{ fontFamily: "\"IBM Plex Mono\", monospace", padding: "24px 32px", maxWidth: 800, margin: "0 auto", fontSize: "0.8125rem", lineHeight: 1.6 }}>
      <style>{`
        @media print {
          body { margin: 0; }
          .no-print { display: none !important; }
          @page { margin: 1.5cm; size: A4; }
        }
      `}</style>

      <div className="no-print" style={{ marginBottom: 16, borderBottom: `1px solid ${C.border}`, paddingBottom: 8 }}>
        <button onClick={() => window.print()} style={{ fontFamily: "inherit", padding: "6px 12px", cursor: "pointer", border: `1px solid ${C.border}`, borderRadius: 4, background: C.elevated, color: C.text }}>
          {t("print_btn")}
        </button>
        <button onClick={() => window.close()} style={{ fontFamily: "inherit", padding: "6px 12px", cursor: "pointer", border: `1px solid ${C.border}`, borderRadius: 4, background: "transparent", color: C.muted, marginLeft: 8 }}>
          {t("close_btn")}
        </button>
      </div>

      <div style={{ textAlign: "center", marginBottom: 24 }}>
        <h1 style={{ fontSize: "1.25rem", fontWeight: 700, margin: 0 }}>{t("post_discharge_summary")}</h1>
        <div style={{ color: C.muted, fontSize: "0.75rem" }}>{t("aarogya_bandhu")} · {new Date().toLocaleDateString()}</div>
      </div>

      {/* patient info */}
      <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 20 }}>
        <tbody>
          <tr><Td label={t("patient_label")}>{p.name}</Td><Td label={t("age_sex")}>{p.age ?? "?"} / {p.sex ?? "—"}</Td></tr>
          <tr><Td label={t("caregiver_label")}>{p.caregiver_name}</Td><Td label={t("phone_label")}>{p.caregiver_phone}</Td></tr>
          <tr><Td label={t("abha_label")}>{p.abha_number || "—"}</Td><Td label={t("ward_label")}>{en?.ward || "—"}</Td></tr>
          <tr><Td label={t("condition_label_print")}>{en?.condition_label || "—"}</Td><Td label={t("protocol_label")}>{en?.protocol_id || "—"}</Td></tr>
        </tbody>
      </table>

      {/* medications */}
      {en && en.meds.length > 0 && (
        <>
          <h3 style={{ fontSize: "0.875rem", fontWeight: 600, marginBottom: 8, borderTop: `1px solid ${C.border}`, paddingTop: 12 }}>{t("medications_heading")}</h3>
          <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 20 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                <th style={thStyle}>{t("name")}</th><th style={thStyle}>{t("type")}</th><th style={thStyle}>{t("doses_day")}</th>
              </tr>
            </thead>
            <tbody>
              {en.meds.map((m, i) => (
                <tr key={i} style={{ borderBottom: `1px solid ${C.borderMuted}` }}>
                  <td style={tdStyle}>{m.name}</td><td style={tdStyle}>{m.type}</td>
                  <td style={tdStyle}>{m.doses}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* call history */}
      {sortedCalls.length > 0 && (
        <>
          <h3 style={{ fontSize: "0.875rem", fontWeight: 600, marginBottom: 8, borderTop: `1px solid ${C.border}`, paddingTop: 12 }}>{t("followup_call_history")}</h3>
          <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 20 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                <th style={thStyle}>{t("day_word")}</th><th style={thStyle}>{t("status_word")}</th><th style={thStyle}>{t("risk_word")}</th><th style={thStyle}>{t("date_word")}</th><th style={thStyle}>{t("provider_word")}</th>
              </tr>
            </thead>
            <tbody>
              {sortedCalls.map((c) => (
                <tr key={c.id} style={{ borderBottom: `1px solid ${C.borderMuted}` }}>
                  <td style={tdStyle}><strong>{t("day_word")} {c.day_index}</strong></td>
                  <td style={tdStyle}>{c.status}</td>
                  <td style={tdStyle}>{c.risk || "—"}</td>
                  <td style={tdStyle}>{new Date(c.scheduled_at).toLocaleDateString()}</td>
                  <td style={tdStyle}>{c.provider || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* escalations */}
      {en && en.escalations.length > 0 && (
        <>
          <h3 style={{ fontSize: "0.875rem", fontWeight: 600, marginBottom: 8, borderTop: `1px solid ${C.border}`, paddingTop: 12 }}>{t("escalations_word")}</h3>
          {en.escalations.map((e, i) => (
            <div key={i} style={{ marginBottom: 8, paddingLeft: 12, borderLeft: `2px solid ${e.level === "red" ? C.danger : C.warning}` }}>
              <strong>{e.level}</strong> · {e.status} · {new Date(e.created_at).toLocaleDateString()}
              {e.reasons && <div style={{ color: C.muted, fontSize: "0.75rem" }}>{e.reasons}</div>}
            </div>
          ))}
        </>
      )}

      <div style={{ marginTop: 32, borderTop: `1px solid ${C.border}`, paddingTop: 12, fontSize: "0.6875rem", color: C.muted, textAlign: "center" }}>
        {t("generated_footer")}
      </div>
    </div>
  );
}

const thStyle: React.CSSProperties = { textAlign: "left", padding: "6px 8px", fontWeight: 500, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em", color: "var(--color-muted)" };
const tdStyle: React.CSSProperties = { padding: "6px 8px" };

function Td({ label, children }: { label?: string; children: React.ReactNode }) {
  return (
    <>
      {label && <td style={{ ...tdStyle, color: "var(--color-muted)", fontWeight: 500, width: "15%" }}>{label}</td>}
      <td style={{ ...tdStyle, width: "35%" }}>{children}</td>
    </>
  );
}
