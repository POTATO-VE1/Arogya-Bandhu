import type { ReactNode, ButtonHTMLAttributes, InputHTMLAttributes } from "react";

const C = {
  bg: "var(--color-bg)",
  surface: "var(--color-surface)",
  elevated: "var(--color-elevated)",
  border: "var(--color-border)",
  borderMuted: "var(--color-border-muted)",
  text: "var(--color-text)",
  secondary: "var(--color-secondary)",
  muted: "var(--color-muted)",
  disabled: "var(--color-disabled)",
  accent: "var(--color-accent)",
  accentHover: "var(--color-accent-hover)",
  danger: "var(--color-danger)",
  warning: "var(--color-warning)",
  success: "var(--color-success)",
};

export function Panel({
  title,
  children,
  style,
}: {
  title?: string;
  children: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <section
      style={{
        border: `1px solid ${C.border}`,
        background: C.surface,
        borderRadius: 10,
        padding: 18,
        boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
        ...style,
      }}
    >
      {title && (
        <div
          style={{
            fontSize: "0.75rem",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            fontWeight: 600,
            color: C.muted,
            borderBottom: `1px solid ${C.borderMuted}`,
            paddingBottom: 10,
            marginBottom: 14,
          }}
        >
          {title}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ value, label }: { value: ReactNode; label: string }) {
  return (
    <Panel style={{ padding: 14 }}>
      <div style={{ fontSize: "1.35rem", fontWeight: 700, letterSpacing: "-0.02em" }}>{value}</div>
      <div
        style={{
          fontSize: "0.7rem",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          fontWeight: 600,
          color: C.muted,
          marginTop: 4,
        }}
      >
        {label}
      </div>
    </Panel>
  );
}

export function RiskBadge({ level }: { level: string | null | undefined }) {
  if (level === "green") {
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          background: "rgba(16, 185, 129, 0.12)",
          color: C.success,
          border: "1px solid rgba(16, 185, 129, 0.3)",
          padding: "2px 8px",
          borderRadius: 12,
          fontSize: "0.75rem",
          fontWeight: 600,
        }}
      >
        ● Low Risk
      </span>
    );
  }
  if (level === "yellow") {
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          background: "rgba(245, 158, 11, 0.12)",
          color: C.warning,
          border: "1px solid rgba(245, 158, 11, 0.3)",
          padding: "2px 8px",
          borderRadius: 12,
          fontSize: "0.75rem",
          fontWeight: 600,
        }}
      >
        ● Moderate
      </span>
    );
  }
  if (level === "red") {
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          background: "rgba(239, 68, 68, 0.15)",
          color: C.danger,
          border: "1px solid rgba(239, 68, 68, 0.3)",
          padding: "2px 8px",
          borderRadius: 12,
          fontSize: "0.75rem",
          fontWeight: 600,
        }}
      >
        ● High Risk
      </span>
    );
  }
  return <span style={{ color: C.disabled, fontSize: "0.75rem" }}>—</span>;
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost";
};
export function Button({ variant = "primary", children, style, ...rest }: BtnProps) {
  const base: React.CSSProperties = {
    fontFamily: "inherit",
    fontSize: "0.8125rem",
    fontWeight: 600,
    padding: "8px 14px",
    cursor: rest.disabled ? "not-allowed" : "pointer",
    opacity: rest.disabled ? 0.5 : 1,
    borderRadius: 6,
    border: `1px solid ${variant === "primary" ? C.accent : C.border}`,
    background: variant === "primary" ? C.accent : C.elevated,
    color: variant === "primary" ? "#ffffff" : C.text,
    transition: "all 0.15s ease-in-out",
    boxShadow: variant === "primary" ? "0 2px 6px rgba(37, 99, 235, 0.3)" : "none",
    ...style,
  };
  return (
    <button style={base} {...rest}>
      {children}
    </button>
  );
}

type InProps = InputHTMLAttributes<HTMLInputElement> & {
  label?: string;
  error?: string;
};
export function Input({ label, error, ...rest }: InProps) {
  return (
    <label style={{ display: "block", marginBottom: 12 }}>
      {label && (
        <span
          style={{
            display: "block",
            fontSize: "0.75rem",
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            color: C.muted,
            marginBottom: 4,
          }}
        >
          {label}
        </span>
      )}
      <input
        style={{
          width: "100%",
          boxSizing: "border-box",
          background: C.bg,
          border: `1px solid ${error ? C.danger : C.border}`,
          borderRadius: 6,
          padding: "10px 12px",
          color: C.text,
          fontFamily: "inherit",
          fontSize: "0.875rem",
          outline: "none",
        }}
        {...rest}
      />
      {error && (
        <div style={{ color: C.danger, fontSize: "0.8125rem", marginTop: 4 }}>
          {error}
        </div>
      )}
    </label>
  );
}

export function Select({
  label,
  children,
  ...rest
}: InputHTMLAttributes<HTMLSelectElement> & { label?: string }) {
  return (
    <label style={{ display: "block", marginBottom: 12 }}>
      {label && (
        <span
          style={{
            display: "block",
            fontSize: "0.75rem",
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            color: C.muted,
            marginBottom: 4,
          }}
        >
          {label}
        </span>
      )}
      <select
        style={{
          width: "100%",
          boxSizing: "border-box",
          background: C.bg,
          border: `1px solid ${C.border}`,
          borderRadius: 6,
          padding: "10px 12px",
          color: C.text,
          fontFamily: "inherit",
          fontSize: "0.875rem",
        }}
        {...(rest as any)}
      >
        {children}
      </select>
    </label>
  );
}

export function KeyHint({ children }: { children: ReactNode }) {
  return (
    <div style={{ color: C.accent, fontSize: "0.8125rem", marginTop: 8, fontWeight: 500 }}>
      [*] {children}
    </div>
  );
}

export function LogLine({ ts, children, tone = "secondary" }: {
  ts?: string;
  children: ReactNode;
  tone?: "secondary" | "danger" | "success" | "warning";
}) {
  const col = { secondary: C.secondary, danger: C.danger, success: C.success, warning: C.warning }[tone];
  return (
    <div style={{ color: col, fontSize: "0.8125rem", lineHeight: 1.5, fontFamily: "var(--font-mono)" }}>
      {ts && <span style={{ color: C.disabled }}>{ts} </span>}
      {children}
    </div>
  );
}

export function Card({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return (
    <div
      style={{
        border: `1px solid ${C.border}`,
        background: C.surface,
        borderRadius: 8,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export function Badge({ color, children }: { color: string; children: ReactNode }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 12,
        fontSize: "0.6875rem",
        fontWeight: 700,
        letterSpacing: "0.06em",
        color,
        background: `${color}18`,
        border: `1px solid ${color}40`,
      }}
    >
      {children}
    </span>
  );
}

export function Confirm({
  message,
  onConfirm,
  onCancel,
}: {
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
    >
      <Card style={{ width: 380, padding: 24 }}>
        <div style={{ fontSize: "0.875rem", marginBottom: 16, lineHeight: 1.5 }}>{message}</div>
        <div style={{ display: "flex", gap: 8 }}>
          <Button variant="primary" onClick={onConfirm} style={{ flex: 1 }}>
            Confirm
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </Card>
    </div>
  );
}

export { C };