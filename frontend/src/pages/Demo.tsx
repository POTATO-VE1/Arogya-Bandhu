import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api, nowHHMM } from "../api";
import { C, KeyHint, LogLine, Panel } from "../components";
import { t } from "../i18n";

type Line = { ts: string; dir: "▸" | "◂"; text: string; tone?: "secondary" | "danger" | "success" | "warning" };
type Expect = { node_id: string; options: { digit: string; reason?: string | null; clip?: string | null; next?: string | null }[] };

export function Demo() {
  const nav = useNavigate();
  const [searchParams] = useSearchParams();
  const callId = searchParams.get("call");
  const [lines, setLines] = useState<Line[]>([]);
  const [expect, setExpect] = useState<Expect | null>(null);
  const [ended, setEnded] = useState<{ risk?: string; reasons?: string[] } | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!callId) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/sim-call?call_id=${callId}`);
    wsRef.current = ws;
    ws.onmessage = async (ev) => {
      let m: any;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "play") {
        setLines((l) => [...l, { ts: ts(), dir: "▸", text: `[${m.clip}] “${m.en}”`, tone: "secondary" }]);
        if (audioRef.current) {
          audioRef.current.src = `/audio/${m.clip}.mp3`;
          try { await audioRef.current.play(); } catch { /* autoplay policy */ }
        }
      } else if (m.type === "expect_digit") {
        setLines((l) => [...l, { ts: ts(), dir: "▸", text: `[${m.node_id}] waiting for answer…`, tone: "secondary" }]);
        setExpect({ node_id: m.node_id, options: m.options });
      } else if (m.type === "end") {
        setExpect(null);
        setEnded({ risk: m.risk, reasons: m.reasons });
        setLines((l) => [...l, { ts: ts(), dir: "▸", text: `call ended`, tone: "warning" }]);
      }
    };
    ws.onclose = () => setExpect(null);
    return () => ws.close();
  }, [callId]);

  function sendDigit(d: string) {
    setLines((l) => [...l, { ts: ts(), dir: "◂", text: `pressed ${d}`, tone: "success" }]);
    setExpect(null);
    wsRef.current?.send(JSON.stringify({ type: "digit", digit: d }));
  }

  // T7: start another sim for the same patient after a call ends.
  // We need the enrollment_id, which we don't have in the URL — the Board
  // passes call_id but not enrollment_id. Read the enrollment from the call
  // by fetching the patient detail (which exposes enrollments[0].id) or,
  // simpler, store the enrollment_id we used in the URL query if provided.
  const [rerunEid, setRerunEid] = useState<string | null>(null);
  useEffect(() => {
    // The Demo page can be opened with ?call=<id>&eid=<id> so rerun has the eid
    // without an extra round trip.
    const sp = new URLSearchParams(location.search);
    setRerunEid(sp.get("eid"));
  }, [location.search]);
  async function runAnother() {
    if (!rerunEid) {
      // Fallback: parse the call_id and look it up via /api/patients/.../timeline
      // is overkill; just navigate back to the board where the user can click [ sim ].
      nav("/board");
      return;
    }
    try {
      const r = await api<{ call_id: string }>("/api/demo/trigger-call", {
        method: "POST",
        body: JSON.stringify({ enrollment_id: rerunEid, channel: "sim" }),
      });
      nav(`/demo?call=${r.call_id}&eid=${rerunEid}`);
    } catch (ex: any) {
      // best-effort fallback
      nav("/board");
    }
  }

  if (!callId) {
    return (
      <Panel title={t("demo_console")}>
        <KeyHint>{t("demo_hint")}</KeyHint>
        <div style={{ marginTop: 12 }}>
          <button style={ghostBtn} onClick={() => nav("/board")}>{t("back_board")}</button>
        </div>
      </Panel>
    );
  }

  return (
    <div>
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 4 }}>
        {t("demo_console")} <span className="cursor" />
      </h2>
      <div style={{ color: C.warning, fontSize: "0.75rem", marginBottom: 16 }}>
        {t("sim_call_banner")}
      </div>

      <Panel title={t("transcript")}>
        <div style={{ maxHeight: 320, overflowY: "auto" }}>
          {lines.length === 0 ? <div style={{ color: C.muted }}>…</div> :
            lines.map((l, i) => (
              <LogLine key={i} ts={l.ts} tone={l.tone || "secondary"}>
                {l.dir} {l.text}
              </LogLine>
            ))}
        </div>
      </Panel>

      {expect && (
        <Panel title={`${t("current_question")} · ${expect.node_id}`} style={{ marginTop: 16 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            {expect.options.map((o) => (
              <button
                key={o.digit}
                onClick={() => sendDigit(o.digit)}
                style={{
                  fontFamily: "inherit", fontSize: "0.9375rem", padding: "16px 24px",
                  cursor: "pointer", borderRadius: 4,
                  border: `1px solid ${C.border}`, background: C.elevated, color: C.text,
                  minWidth: 140,
                }}
              >
                <span style={{ color: C.accent, fontWeight: 600 }}>[ {o.digit} ]</span>{" "}
                {o.reason ? <span style={{ color: C.muted }}>{o.reason}</span> : <span>{t("option")} {o.digit}</span>}
              </button>
            ))}
          </div>
        </Panel>
      )}

      {ended && (
        <Panel title={t("call_result")} style={{ marginTop: 16 }}>
          {ended.risk && (
            <div style={{
              color: ended.risk === "red" ? C.danger : ended.risk === "yellow" ? C.warning : C.success,
              fontWeight: 600, fontSize: "1rem",
            }}>
              {t("risk_prefix")} [{ended.risk?.toUpperCase()}] {ended.risk === "red" ? t("esc_created") : ""}
            </div>
          )}
          {ended.reasons && (
            <div style={{ color: C.muted, fontSize: "0.8125rem", marginTop: 4 }}>
              {t("reasons")} {ended.reasons.join(", ")}
            </div>
          )}
          <div style={{ marginTop: 12, display: "flex", gap: 12 }}>
            <button style={ghostBtn} onClick={() => nav("/board")}>{t("back_board")}</button>
            <button style={{ ...ghostBtn, borderColor: C.accent, color: C.accent }} onClick={runAnother}>
              [ run another sim ]
            </button>
          </div>
        </Panel>
      )}

      <KeyHint>{t("demo_audio_hint")}</KeyHint>
      <audio ref={audioRef} style={{ display: "none" }} />
    </div>
  );
}

const ts = () => nowHHMM();
const ghostBtn: React.CSSProperties = {
  fontFamily: "inherit", fontSize: "0.8125rem", background: "transparent",
  border: `1px solid ${C.border}`, color: C.secondary, borderRadius: 4, padding: "8px 12px", cursor: "pointer",
};