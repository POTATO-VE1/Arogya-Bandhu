import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { Badge, C, Panel, RiskBadge } from "../components";
import { t } from "../i18n";
import { useAuth } from "../App";

type ActivityItem = {
  id: string;
  action: string;
  entity_id: string | null;
  entity_name: string | null;
  meta: Record<string, any> | null;
  created_at: string;
};

type PatientRow = {
  enrollment_id: string;
  patient_id: string;
  patient_name: string;
  phone: string;
  protocol_id: string;
  ward: string | null;
  condition: string | null;
  discharge_date: string | null;
  day_index_next: number | null;
  last_call_status: string | null;
  last_risk: string | null;
  open_escalation: boolean;
  outcome: string | null;
  created_by: string | null;
  created_at: string;
};

const ACTION_CONFIG: Record<string, { icon: string; label: string; labelKo: string; tone: string }> = {
  enroll:          { icon: "[+]", label: "Enrolled patient", labelKo: "ರೋಗಿ ದಾಖಲಿಸಿದ", tone: "success" },
  trigger_call:    { icon: "[T]", label: "Triggered call", labelKo: "ಕರೆ ಪ್ರಾರಂಭಿಸಿದ", tone: "secondary" },
  ack:             { icon: "[!]", label: "Acknowledged escalation", labelKo: "ಎಚ್ಚರಿಕೆ ಗಮನಿಸಿದ", tone: "warning" },
  resolve:         { icon: "[OK]", label: "Resolved escalation", labelKo: "ಎಚ್ಚರಿಕೆ ಪರಿಹರಿಸಿದ", tone: "success" },
  set_outcome:     { icon: "[*]", label: "Set outcome", labelKo: "ಫಲಿತಾಂಶ ಹಾಕಿದ", tone: "secondary" },
  login:           { icon: "[K]", label: "Signed in", labelKo: "ಸೈನ್ ಇನ್ ಮಾಡಿದ", tone: "secondary" },
  demo_trigger:    { icon: "[S]", label: "Simulated call", labelKo: "ಸಿಮ್ಯುಲೇಟೆಡ್ ಕರೆ", tone: "secondary" },
  import:          { icon: "[I]", label: "Imported patients", labelKo: "ರೋಗಿಗಳನ್ನು ಆಮದು ಮಾಡಿದ", tone: "secondary" },
  verify_number:   { icon: "[Y]", label: "Verified number", labelKo: "ಸಂಖ್ಯೆ ಪರಿಶೀಲಿಸಿದ", tone: "success" },
};

function getActionConfig(action: string) {
  return ACTION_CONFIG[action] || { icon: "[*]", label: action, labelKo: action, tone: "secondary" };
}

const OUTCOME_COLORS: Record<string, string> = {
  recovered: C.success,
  readmitted: C.danger,
  referred: C.warning,
  deceased: C.muted,
  lost_to_followup: C.muted,
  transferred: C.muted,
};

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

export function StaffDashboard() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [patients, setPatients] = useState<PatientRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [act, pts] = await Promise.all([
          api<ActivityItem[]>("/api/staff/activity").catch(() => []),
          api<PatientRow[]>("/api/staff/patients").catch(() => []),
        ]);
        setActivities(act);
        setPatients(pts);
        setErr(null);
      } catch (ex: any) {
        setErr(ex?.message || "failed to load");
      } finally {
        setLoading(false);
      }
    }
    load();
    const iv = setInterval(load, 15000);
    return () => clearInterval(iv);
  }, []);

  if (loading) {
    return (
      <Panel>
        <div style={{ color: C.muted }}>{t("loading")}</div>
      </Panel>
    );
  }

  if (err) {
    return <Panel><div style={{ color: C.danger }}>{err}</div></Panel>;
  }

  return (
    <div>
      {/* ── Header ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <h2 style={{ fontSize: "1.125rem", fontWeight: 600, margin: 0 }}>{t("my_dashboard")}</h2>
        {user && (
          <>
            <span style={{ color: C.muted, fontSize: "0.8125rem" }}>· {user.display_name}</span>
            <Badge color={C.accent}>{user.role}</Badge>
          </>
        )}
      </div>

      {/* ── Activity Feed ── */}
      <Panel title={t("my_activity")} style={{ marginBottom: 16 }}>
        {activities.length === 0 ? (
          <div style={{ color: C.muted, fontSize: "0.8125rem" }}>{t("no_activity_yet")}</div>
        ) : (
          <div>
            {activities.map((item) => {
              const cfg = getActionConfig(item.action);
              return (
                <div
                  key={item.id}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 10,
                    padding: "8px 0",
                    borderTop: `1px solid ${C.borderMuted}`,
                    fontSize: "0.8125rem",
                  }}
                >
                  <span style={{ fontSize: "1rem", flexShrink: 0, width: 24, textAlign: "center" }}>
                    {cfg.icon}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <span>{cfg.label}</span>
                    {item.entity_name && (
                      <span style={{ color: C.muted }}> · {item.entity_name}</span>
                    )}
                  </div>
                  <span style={{ color: C.disabled, fontSize: "0.75rem", flexShrink: 0 }}>
                    {relativeTime(item.created_at)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      {/* ── My Patients ── */}
      <Panel title={t("my_patients")}>
        {patients.length === 0 ? (
          <div style={{ color: C.muted, fontSize: "0.8125rem" }}>{t("no_patients_yet")}</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8125rem" }}>
            <thead>
              <tr style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em" }}>
                <Th>{t("patients")}</Th>
                <Th>{t("ward")}</Th>
                <Th>{t("protocol")}</Th>
                <Th>{t("next_call")}</Th>
                <Th>{t("last_risk")}</Th>
                <Th>{t("status")}</Th>
                <Th>{t("outcome")}</Th>
              </tr>
            </thead>
            <tbody>
              {patients.map((r) => (
                <tr
                  key={r.enrollment_id}
                  style={{ borderTop: `1px solid ${C.borderMuted}`, cursor: "pointer" }}
                  onClick={() => r.patient_id && nav(`/patients/${r.patient_id}`)}
                >
                  <Td>
                    <span style={{ color: C.text }}>{r.patient_name}</span>
                  </Td>
                  <Td style={{ color: C.muted }}>{r.ward || "—"}</Td>
                  <Td>{r.protocol_id}</Td>
                  <Td>{r.day_index_next ? `D${r.day_index_next}` : "—"}</Td>
                  <Td>
                    {r.open_escalation ? <RiskBadge level="red" /> : <RiskBadge level={r.last_risk} />}
                  </Td>
                  <Td>
                    {r.open_escalation ? (
                      <span style={{ fontSize: "0.6875rem", padding: "2px 6px", borderRadius: 3, background: `${C.danger}22`, color: C.danger }}>
                        [RED] ESC
                      </span>
                    ) : (
                      <span style={{ color: C.disabled }}>—</span>
                    )}
                  </Td>
                  <Td>
                    {r.outcome ? (
                      <span style={{ fontSize: "0.6875rem", padding: "2px 6px", borderRadius: 3, background: `${OUTCOME_COLORS[r.outcome] || C.muted}22`, color: OUTCOME_COLORS[r.outcome] || C.muted }}>
                        {t(r.outcome)}
                      </span>
                    ) : (
                      <span style={{ color: C.disabled }}>—</span>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}

const Th = ({ children }: any) => (
  <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>{children}</th>
);
const Td = ({ children, style }: any) => (
  <td style={{ padding: "6px 8px", ...style }}>{children}</td>
);
