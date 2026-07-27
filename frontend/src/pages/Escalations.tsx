import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { Button, C, LogLine, Panel } from "../components";
import { t } from "../i18n";

type Esc = {
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
};

export function Escalations() {
  const [rows, setRows] = useState<Esc[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function refresh() {
    try {
      setRows(await api<Esc[]>("/api/escalations"));
      setErr(null);
    } catch (ex: any) {
      setErr(ex?.message || "failed");
    }
  }
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  async function ack(id: string) {
    try {
      await api(`/api/escalations/${id}/ack`, { method: "POST" });
      refresh();
    } catch (ex) {
      setErr(ex instanceof ApiError ? ex.message : "ack failed");
    }
  }

  if (!rows)
    return (
      <Panel>
        <div style={{ color: C.muted }}>…</div>
      </Panel>
    );

  const open = rows.filter((r) => r.status === "open");
  const done = rows.filter((r) => r.status === "acked");

  return (
    <div>
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 16 }}>{t("escalations")}</h2>
      {err && <LogLine tone="danger">{err}</LogLine>}
      <Panel title={`${t("open")} · ${open.length}`} style={{ marginBottom: 16 }}>
        {open.length === 0 ? (
          <div style={{ color: C.muted }}>{t("no_esc_all_clear")}</div>
        ) : (
          open.map((r) => (
            <div
              key={r.id}
              style={{
                borderTop: `1px solid ${C.borderMuted}`,
                padding: "10px 0",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 12,
                flexWrap: "wrap",
              }}
            >
              <div>
                <span style={{ color: C.danger, fontWeight: 600 }}>[RED]</span>{" "}
                <strong>{r.patient_name}</strong> · {r.protocol_id} ·{" "}
                <span style={{ color: C.secondary }}>{r.reasons.join(", ")}</span>
                <div style={{ color: C.muted, fontSize: "0.75rem", marginTop: 2 }}>
                  {new Date(r.created_at).toLocaleString()} · {t("caregiver")} {r.caregiver_phone}
                </div>
              </div>
              <Button variant="ghost" onClick={() => ack(r.id)}>
                {t("ack")}
              </Button>
            </div>
          ))
        )}
      </Panel>
      <Panel title={`${t("acked")} · ${done.length}`}>
        {done.length === 0 ? (
          <div style={{ color: C.muted }}>—</div>
        ) : (
          done.map((r) => (
            <LogLine key={r.id} tone="secondary">
              {new Date(r.created_at).toLocaleString()} {r.patient_name} · {t("acked")}{" "}
              {r.acked_at ? `${Math.round((new Date(r.acked_at).getTime() - new Date(r.created_at).getTime()) / 60000)}m` : ""}
            </LogLine>
          ))
        )}
      </Panel>
    </div>
  );
}