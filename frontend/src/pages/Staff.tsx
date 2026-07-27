import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useAuth, useToast } from "../App";
import { Button, Input, Select, Card, Badge, Confirm } from "../components";
import { t } from "../i18n";

type StaffMember = {
  id: string;
  username: string;
  display_name: string;
  role: string;
  hospital_code: string;
  created_at: string;
};

const ROLES = [
  { value: "admin", label: "Admin" },
  { value: "doctor", label: "Doctor" },
  { value: "nurse", label: "Nurse" },
  { value: "staff", label: "Staff" },
];

export function Staff() {
  const { user } = useAuth();
  const { show } = useToast();
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ username: "", display_name: "", role: "staff", password: "" });
  const [submitting, setSubmitting] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<StaffMember | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api<StaffMember[]>("/api/staff-mgmt");
      setStaff(data);
    } catch {
      show("Failed to load staff", "error");
    } finally {
      setLoading(false);
    }
  }, [show]);

  useEffect(() => { load(); }, [load]);

  const create = async () => {
    if (!form.username || !form.display_name || !form.password) {
      show("All fields required", "error");
      return;
    }
    setSubmitting(true);
    try {
      await api("/api/staff-mgmt", {
        method: "POST",
        body: JSON.stringify(form),
      });
      show(`Created ${form.display_name}`, "success");
      setForm({ username: "", display_name: "", role: "staff", password: "" });
      setShowCreate(false);
      load();
    } catch (e: any) {
      show(e?.message || "Failed to create", "error");
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    try {
      await api(`/api/staff-mgmt/${deleteTarget.id}`, { method: "DELETE" });
      show(`Removed ${deleteTarget.display_name}`, "success");
      setDeleteTarget(null);
      load();
    } catch (e: any) {
      show(e?.message || "Failed to delete", "error");
    }
  };

  const roleColor = (role: string) => {
    switch (role) {
      case "admin": return "var(--red)";
      case "doctor": return "var(--accent)";
      case "nurse": return "var(--success)";
      default: return "var(--muted)";
    }
  };

  if (user?.role !== "admin") {
    return (
      <div style={{ padding: 24, color: "var(--muted)" }}>
        Admin access required.
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <h2 style={{ margin: 0, fontSize: "1rem", letterSpacing: "0.08em", textTransform: "uppercase" }}>
          {t("staff_management")}
        </h2>
        <Button variant="primary" onClick={() => setShowCreate(true)}>
          + {t("add_staff")}
        </Button>
      </div>

      {loading ? (
        <div style={{ color: "var(--muted)" }} className="loading-pulse">{t("loading")}</div>
      ) : staff.length === 0 ? (
        <div style={{ color: "var(--muted)", padding: 24 }}>No staff members found.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {staff.map((s) => (
            <Card key={s.id} style={{ padding: "12px 16px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontWeight: 600 }}>{s.display_name}</div>
                <div style={{ color: "var(--muted)", fontSize: "0.75rem" }}>@{s.username}</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <Badge color={roleColor(s.role)}>{s.role.toUpperCase()}</Badge>
                <Button
                  variant="ghost"
                  style={{ color: "var(--danger)", fontSize: "0.75rem" }}
                  onClick={() => setDeleteTarget(s)}
                >
                  remove
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      {showCreate && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
          display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
        }}>
          <Card style={{ width: 400, padding: 24 }}>
            <h3 style={{ margin: "0 0 16px", fontSize: "0.875rem", textTransform: "uppercase", letterSpacing: "0.08em" }}>
              {t("add_staff")}
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Input
                placeholder="Username (lowercase, alphanumeric)"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase().replace(/[^a-z0-9._-]/g, "") })}
              />
              <Input
                placeholder="Display Name"
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              />
              <Select
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
              >
                {ROLES.map((r) => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </Select>
              <Input
                type="password"
                placeholder="Password (min 6 chars)"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <Button
                  variant="primary"
                  onClick={create}
                  disabled={submitting}
                  style={{ flex: 1 }}
                >
                  {submitting ? "..." : t("create")}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => { setShowCreate(false); setForm({ username: "", display_name: "", role: "staff", password: "" }); }}
                >
                  {t("cancel")}
                </Button>
              </div>
            </div>
          </Card>
        </div>
      )}

      {deleteTarget && (
        <Confirm
          message={`Remove ${deleteTarget.display_name} (@${deleteTarget.username})? This cannot be undone.`}
          onConfirm={remove}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
