import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { Button, C, LogLine, Panel } from "../components";

type MappingInfo = { field: string | null; confidence: number };
type RowResult = {
  index: number;
  raw: Record<string, string>;
  mapped: Record<string, any>;
  valid: boolean;
  warnings: string[];
  errors: string[];
};

const FIELDS = [
  { value: "", label: "— skip —" },
  { value: "name", label: "Patient Name" },
  { value: "age", label: "Age" },
  { value: "sex", label: "Sex (M/F/O)" },
  { value: "caregiver_name", label: "Caregiver Name" },
  { value: "caregiver_phone", label: "Caregiver Phone" },
  { value: "condition_label", label: "Condition / Diagnosis" },
  { value: "protocol_id", label: "Protocol" },
  { value: "discharge_date", label: "Discharge Date" },
  { value: "ward", label: "Ward" },
  { value: "med_name", label: "Medication Name" },
  { value: "med_type", label: "Med Type (antibiotic/other)" },
  { value: "aware_category", label: "AWaRe Category" },
  { value: "course_days", label: "Course Days" },
  { value: "doses_per_day", label: "Doses/Day" },
];

function confidenceColor(c: number) {
  if (c >= 0.9) return C.success;
  if (c >= 0.6) return C.warning;
  return C.danger;
}

export function Import() {
  const nav = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  // step state
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [fileId, setFileId] = useState<string | null>(null);
  const [fileName, setFileName] = useState("");
  const [totalRows, setTotalRows] = useState(0);
  const [headers, setHeaders] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string, MappingInfo>>({});
  const [rows, setRows] = useState<RowResult[]>([]);
  const [protocols, setProtocols] = useState<string[]>([]);
  const [defaultProtocol, setDefaultProtocol] = useState("wound_care");
  const [defaultWard, setDefaultWard] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ imported: number; skipped: number } | null>(null);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const [importErr, setImportErr] = useState<string | null>(null);

  const handleFile = useCallback(async (file: File) => {
    setBusy(true);
    setUploadErr(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await api<any>("/api/import/preview", { method: "POST", body: form });
      setFileId(res.file_id);
      setFileName(res.filename);
      setTotalRows(res.total_rows);
      setHeaders(res.headers);
      setMapping(res.mapping_suggestions);
      setRows(res.rows);
      setProtocols(res.protocols);
      setStep(2);
    } catch (e: any) {
      setUploadErr(e?.message || "upload failed");
    } finally {
      setBusy(false);
    }
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  }, [handleFile]);

  const onFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) handleFile(f);
  }, [handleFile]);

  const updateMapping = (header: string, field: string) => {
    setMapping(prev => ({ ...prev, [header]: { field: field || null, confidence: field ? 1 : 0 } }));
  };

  const validCount = rows.filter(r => r.valid).length;
  const warnCount = rows.filter(r => r.valid && r.warnings.length > 0).length;
  const errorCount = rows.filter(r => !r.valid).length;

  const doImport = async () => {
    if (!fileId) return;
    setBusy(true);
    setImportErr(null);
    try {
      const res = await api<{ imported: number; skipped: number }>("/api/import/confirm", {
        method: "POST",
        body: JSON.stringify({
          file_id: fileId,
          mapping,
          selected_indices: rows.map(r => r.index),
          default_protocol: defaultProtocol,
          default_ward: defaultWard || null,
        }),
      });
      setResult(res);
      setStep(3);
    } catch (e: any) {
      setImportErr(e?.message || "import failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em", color: C.muted, marginBottom: 16 }}>
        bulk import
      </div>

      {/* step indicator */}
      <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
        {([1, 2, 3] as const).map(s => (
          <div key={s} style={{
            padding: "4px 12px",
            fontSize: "0.75rem",
            border: `1px solid ${step === s ? C.accent : C.borderMuted}`,
            background: step === s ? C.elevated : "transparent",
            color: step === s ? C.text : C.disabled,
          }}>
            {s === 1 ? "upload" : s === 2 ? "map columns" : "done"}
          </div>
        ))}
      </div>

      {/* step 1: upload */}
      {step === 1 && (
        <Panel>
          {uploadErr && (
            <div style={{ marginBottom: 12 }}>
              <LogLine tone="danger">{uploadErr}</LogLine>
              <Button variant="ghost" style={{ marginTop: 8 }} onClick={() => setUploadErr(null)}>[ dismiss ]</Button>
            </div>
          )}
          <div
            onDragOver={e => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
            style={{
              border: `2px dashed ${dragOver ? C.accent : C.border}`,
              background: dragOver ? C.elevated : C.bg,
              padding: "48px 24px",
              textAlign: "center",
              cursor: "pointer",
              transition: "border-color 0.15s, background 0.15s",
            }}
          >
            <div style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 8 }}>
              {busy ? "parsing…" : "drop CSV or Excel file here"}
            </div>
            <div style={{ fontSize: "0.8125rem", color: C.muted }}>
              or click to browse · .csv .xlsx · max 10MB
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.xlsx,.xls"
              onChange={onFileInput}
              style={{ display: "none" }}
            />
          </div>
          <div style={{ marginTop: 16 }}>
            <div style={{ fontSize: "0.75rem", color: C.muted, marginBottom: 6 }}>download a template:</div>
            <div style={{ display: "flex", gap: 8 }}>
              {protocols.map(p => (
                <a key={p} href={`/api/import/template/${p}`} style={{
                  padding: "4px 10px",
                  fontSize: "0.75rem",
                  border: `1px solid ${C.border}`,
                  color: C.accent,
                  textDecoration: "none",
                }}>
                  {p}.csv
                </a>
              ))}
            </div>
          </div>
        </Panel>
      )}

      {/* step 2: map + preview */}
      {step === 2 && (
        <>
          <Panel title="column mapping" style={{ marginBottom: 16 }}>
            <div style={{ fontSize: "0.8125rem", color: C.muted, marginBottom: 12 }}>
              {fileName} · {totalRows} rows · {headers.length} columns detected
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr auto 1fr auto", gap: "6px 12px", alignItems: "center" }}>
              {headers.map(h => {
                const info = mapping[h];
                return (
                  <div key={h} style={{ display: "contents" }}>
                    <div style={{
                      fontSize: "0.8125rem",
                      padding: "6px 8px",
                      background: C.bg,
                      border: `1px solid ${C.borderMuted}`,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}>
                      {h}
                    </div>
                    <div style={{ fontSize: "0.75rem", color: C.disabled }}>→</div>
                    <select
                      value={info?.field || ""}
                      onChange={e => updateMapping(h, e.target.value)}
                      style={{
                        fontSize: "0.8125rem",
                        padding: "6px 8px",
                        background: C.bg,
                        border: `1px solid ${C.border}`,
                        color: C.text,
                        fontFamily: "inherit",
                      }}
                    >
                      {FIELDS.map(f => (
                        <option key={f.value} value={f.value}>{f.label}</option>
                      ))}
                    </select>
                    <div style={{
                      fontSize: "0.6875rem",
                      color: info?.field ? confidenceColor(info.confidence) : C.disabled,
                      minWidth: 40,
                    }}>
                      {info?.field ? `${Math.round(info.confidence * 100)}%` : "skip"}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* defaults */}
            <div style={{ marginTop: 16, display: "flex", gap: 12, alignItems: "center" }}>
              <div style={{ fontSize: "0.75rem", color: C.muted }}>default protocol:</div>
              <select
                value={defaultProtocol}
                onChange={e => setDefaultProtocol(e.target.value)}
                style={{
                  fontSize: "0.8125rem", padding: "4px 8px", background: C.bg,
                  border: `1px solid ${C.border}`, color: C.text, fontFamily: "inherit",
                }}
              >
                {protocols.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
              <div style={{ fontSize: "0.75rem", color: C.muted }}>default ward:</div>
              <input
                value={defaultWard}
                onChange={e => setDefaultWard(e.target.value)}
                placeholder="OPD"
                style={{
                  fontSize: "0.8125rem", padding: "4px 8px", background: C.bg,
                  border: `1px solid ${C.border}`, color: C.text, fontFamily: "inherit",
                  width: 80,
                }}
              />
            </div>
          </Panel>

          {/* preview table */}
          <Panel title={`preview — ${validCount} valid · ${warnCount} warnings · ${errorCount} errors`}>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8125rem" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: "6px 8px", borderBottom: `1px solid ${C.border}`, color: C.muted }}>#</th>
                    <th style={{ textAlign: "left", padding: "6px 8px", borderBottom: `1px solid ${C.border}`, color: C.muted }}>status</th>
                    {["name", "caregiver_phone", "condition_label", "protocol_id", "ward", "med_name"].map(f => (
                      <th key={f} style={{ textAlign: "left", padding: "6px 8px", borderBottom: `1px solid ${C.border}`, color: C.muted }}>
                        {f.replace("_", " ")}
                      </th>
                    ))}
                    <th style={{ textAlign: "left", padding: "6px 8px", borderBottom: `1px solid ${C.border}`, color: C.muted }}>issues</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.index} style={{ background: !r.valid ? "rgba(255,69,58,0.06)" : r.warnings.length ? "rgba(255,159,10,0.06)" : "transparent" }}>
                      <td style={{ padding: "6px 8px", borderBottom: `1px solid ${C.borderMuted}`, color: C.disabled }}>{r.index + 1}</td>
                      <td style={{ padding: "6px 8px", borderBottom: `1px solid ${C.borderMuted}` }}>
                        <span style={{ color: r.valid ? (r.warnings.length ? C.warning : C.success) : C.danger }}>
                          {r.valid ? (r.warnings.length ? "⚠" : "✓") : "✗"}
                        </span>
                      </td>
                      {["name", "caregiver_phone", "condition_label", "protocol_id", "ward", "med_name"].map(f => (
                        <td key={f} style={{ padding: "6px 8px", borderBottom: `1px solid ${C.borderMuted}`, maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {r.mapped[f] || <span style={{ color: C.disabled }}>—</span>}
                        </td>
                      ))}
                      <td style={{ padding: "6px 8px", borderBottom: `1px solid ${C.borderMuted}`, fontSize: "0.75rem" }}>
                        {r.errors.map((e, i) => <div key={i} style={{ color: C.danger }}>{e}</div>)}
                        {r.warnings.map((w, i) => <div key={i} style={{ color: C.warning }}>{w}</div>)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div style={{ marginTop: 16, display: "flex", gap: 12 }}>
              {importErr && (
                <div style={{ flex: 1 }}>
                  <LogLine tone="danger">{importErr}</LogLine>
                </div>
              )}
              <Button onClick={doImport} disabled={busy || errorCount > 0}>
                {busy ? "importing…" : `[ import ${validCount} patients ]`}
              </Button>
              <Button variant="ghost" onClick={() => { setStep(1); setFileId(null); }}>
                [ start over ]
              </Button>
            </div>
          </Panel>
        </>
      )}

      {/* step 3: done */}
      {step === 3 && result && (
        <Panel>
          <div style={{ fontSize: "1.25rem", fontWeight: 600, marginBottom: 12 }}>
            import complete
          </div>
          <div style={{ display: "flex", gap: 24, marginBottom: 20 }}>
            <div>
              <div style={{ fontSize: "1.5rem", fontWeight: 600, color: C.success }}>{result.imported}</div>
              <div style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase" }}>imported</div>
            </div>
            <div>
              <div style={{ fontSize: "1.5rem", fontWeight: 600, color: result.skipped ? C.warning : C.disabled }}>{result.skipped}</div>
              <div style={{ fontSize: "0.75rem", color: C.muted, textTransform: "uppercase" }}>skipped</div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 12 }}>
            <Button onClick={() => nav("/board")}>[ view board → ]</Button>
            <Button variant="ghost" onClick={() => { setStep(1); setFileId(null); setResult(null); }}>
              [ import more ]
            </Button>
          </div>
        </Panel>
      )}
    </div>
  );
}
