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
        padding: 16,
        ...style,
      }}
    >
      {title && (
        <div
          style={{
            fontSize: "0.75rem",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            color: C.muted,
            borderBottom: `1px solid ${C.borderMuted}`,
            paddingBottom: 8,
            marginBottom: 12,
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
    <Panel>
      <div style={{ fontSize: "1.25rem", fontWeight: 600 }}>{value}</div>
      <div
        style={{
          fontSize: "0.75rem",
          textTransform: "uppercase",
          letterSpacing: "0.1em",
          color: C.muted,
          marginTop: 4,
        }}
      >
        {label}
      </div>
    </Panel>
  );
}

const RISK: Record<string, { tag: string; color: string }> = {
  green: { tag: "[GRN]", color: C.success },
  yellow: { tag: "[YEL]", color: C.warning },
  red: { tag: "[RED]", color: C.danger },
};
export function RiskBadge({ level }: { level: string | null | undefined }) {
  const r = level && RISK[level];
  if (!r) return <span style={{ color: C.disabled }}>[ — ]</span>;
  return (
    <span style={{ color: r.color, fontWeight: 600, fontSize: "0.8125rem" }}>
      {r.tag}
    </span>
  );
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost";
};
export function Button({ variant = "primary", children, style, ...rest }: BtnProps) {
  const base: React.CSSProperties = {
    fontFamily: "inherit",
    fontSize: "0.8125rem",
    padding: "10px 14px",
    cursor: rest.disabled ? "not-allowed" : "pointer",
    opacity: rest.disabled ? 0.5 : 1,
    borderRadius: 4,
    border: `1px solid ${variant === "primary" ? C.accent : C.border}`,
    background: variant === "primary" ? C.accent : "transparent",
    color: variant === "primary" ? "#fff" : C.secondary,
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
    <label style={{ display: "block", marginBottom: 10 }}>
      {label && (
        <span
          style={{
            display: "block",
            fontSize: "0.75rem",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
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
          borderRadius: 4,
          padding: "10px 12px",
          color: C.text,
          fontFamily: "inherit",
          fontSize: "0.9375rem",
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
    <label style={{ display: "block", marginBottom: 10 }}>
      {label && (
        <span
          style={{
            display: "block",
            fontSize: "0.75rem",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
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
          borderRadius: 4,
          padding: "10px 12px",
          color: C.text,
          fontFamily: "inherit",
          fontSize: "0.9375rem",
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
    <div style={{ color: C.accent, fontSize: "0.8125rem", marginTop: 8 }}>
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
    <div style={{ color: col, fontSize: "0.8125rem", lineHeight: 1.5 }}>
      {ts && <span style={{ color: C.disabled }}>{ts} </span>}
      {children}
    </div>
  );
}

export { C };