import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, NavLink, Navigate, useNavigate } from "react-router-dom";

import { api } from "./api";
import { Button, C } from "./components";
import { Login } from "./pages/Login";
import { Intake } from "./pages/Intake";
import { Board } from "./pages/Board";
import { Escalations } from "./pages/Escalations";
import { PatientDetail } from "./pages/PatientDetail";
import { Sheet } from "./pages/Sheet";
import { Demo } from "./pages/Demo";
import { Import } from "./pages/Import";
import { Amr } from "./pages/Amr";
import { PrintPatient } from "./pages/PrintPatient";

type User = { id: string; display_name: string; role: string; hospital_name: string };
const AuthCtx = createContext<{
  user: User | null;
  loading: boolean;
  setUser: (u: User | null) => void;
}>({ user: null, loading: true, setUser: () => {} });

export function useAuth() {
  return useContext(AuthCtx);
}

const NAV: [string, string][] = [
  ["board", "/board"],
  ["intake", "/intake"],
  ["import", "/import"],
  ["escalations", "/escalations"],
  ["amr", "/amr"],
  ["demo", "/demo"],
];

function Shell({ children }: { children: ReactNode }) {
  const { user, setUser } = useAuth();
  const nav = useNavigate();
  const [sseOk, setSseOk] = useState(true);

  useEffect(() => {
    let es: EventSource | null = null;
    try {
      es = new EventSource("/api/events");
      es.onopen = () => setSseOk(true);
      es.onerror = () => setSseOk(false);
    } catch {
      setSseOk(false);
    }
    return () => { es && es.close(); };
  }, []);

  return (
    <div style={{ minHeight: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {/* sidebar */}
        <aside
          style={{
            width: 220,
            flexShrink: 0,
            borderRight: `1px solid ${C.border}`,
            background: C.surface,
            padding: 16,
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          <div style={{ fontWeight: 600, fontSize: "0.9375rem", letterSpacing: "0.04em", marginBottom: 2 }}>
            ▚▚ AAROGYA BANDHU
          </div>
          <div style={{ color: C.muted, fontSize: "0.75rem", marginBottom: 16 }}>
            {user?.hospital_name}
          </div>

          {NAV.map(([label, to]) => (
            <NavLink key={to} to={to}
              style={({ isActive }) => ({
                display: "block",
                color: isActive ? C.text : C.muted,
                textDecoration: "none",
                fontSize: "0.8125rem",
                padding: "8px 10px",
                borderLeft: isActive ? `2px solid ${C.accent}` : "2px solid transparent",
                background: isActive ? C.elevated : "transparent",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
              })}
            >
              {({ isActive }) => (
                <>{isActive ? "▸ " : "  "}{label}</>
              )}
            </NavLink>
          ))}

          <div style={{ marginTop: "auto", paddingTop: 16, borderTop: `1px solid ${C.borderMuted}` }}>
            <div style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em" }}>
              signed in
            </div>
            <div style={{ fontSize: "0.8125rem", marginTop: 2 }}>{user?.display_name}</div>
            <Button
              variant="ghost"
              style={{ marginTop: 10, width: "100%", padding: "8px 10px" }}
              onClick={async () => {
                await api("/api/auth/logout", { method: "POST" });
                setUser(null);
                nav("/login");
              }}
            >
              [ logout ]
            </Button>
          </div>
        </aside>

        {/* content */}
        <main style={{ flex: 1, maxWidth: 980, width: "100%", margin: "0 auto", padding: 24, overflow: "auto" }}>
          {children}
        </main>
      </div>

      <footer
        style={{
          color: sseOk ? C.success : C.danger,
          fontSize: "0.75rem",
          padding: "8px 16px",
          borderTop: `1px solid ${C.borderMuted}`,
        }}
      >
        {sseOk ? "● connected" : "○ disconnected"}
      </footer>
    </div>
  );
}

function Protected({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div style={{ padding: 24, color: C.muted }} className="loading-pulse">loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <Shell>{children}</Shell>;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    api<User>("/api/auth/me")
      .then((u) => setUser(u))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);
  return (
    <AuthCtx.Provider value={{ user, loading, setUser }}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Navigate to="/board" replace />} />
          <Route path="/board" element={<Protected><Board /></Protected>} />
          <Route path="/intake" element={<Protected><Intake /></Protected>} />
          <Route path="/import" element={<Protected><Import /></Protected>} />
          <Route path="/escalations" element={<Protected><Escalations /></Protected>} />
          <Route path="/amr" element={<Protected><Amr /></Protected>} />
          <Route path="/demo" element={<Protected><Demo /></Protected>} />
          <Route path="/patients/:id" element={<Protected><PatientDetail /></Protected>} />
          <Route path="/sheet/:eid" element={<Protected><Sheet /></Protected>} />
          <Route path="/print/patient/:id" element={<Protected><PrintPatient /></Protected>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthCtx.Provider>
  );
}