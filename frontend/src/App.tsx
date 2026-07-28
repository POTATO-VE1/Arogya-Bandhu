import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, NavLink, Navigate, useNavigate } from "react-router-dom";

import { api } from "./api";
import { Button, C } from "./components";
import { getLang, setLang, onLangChange, t, type Lang } from "./i18n";
import { Login } from "./pages/Login";
import { Intake } from "./pages/Intake";
import { Board } from "./pages/Board";
import { Escalations } from "./pages/Escalations";
import { PatientDetail } from "./pages/PatientDetail";
import { Sheet } from "./pages/Sheet";
import { Demo } from "./pages/Demo";
import { TwilioHealthPage } from "./pages/TwilioHealthPage";
import { Import } from "./pages/Import";
import { PrintPatient } from "./pages/PrintPatient";
import { Staff } from "./pages/Staff";
import { StaffDashboard } from "./pages/StaffDashboard";
import { WardReport } from "./pages/WardReport";
import { DistrictDashboard } from "./pages/DistrictDashboard";

type User = { id: string; display_name: string; role: string; hospital_name: string; ward?: string | null };
const AuthCtx = createContext<{
  user: User | null;
  loading: boolean;
  setUser: (u: User | null) => void;
}>({ user: null, loading: true, setUser: () => {} });

export function useAuth() {
  return useContext(AuthCtx);
}

// ── Language context ──────────────────────────────────────────────────────
const LangCtx = createContext<{ lang: Lang; toggle: () => void }>({
  lang: "en",
  toggle: () => {},
});
export function useLang() {
  return useContext(LangCtx);
}

// ── Toast context ────────────────────────────────────────────────────────
type Toast = { id: number; msg: string; type: "success" | "error" | "info" };
const ToastCtx = createContext<{ show: (msg: string, type?: Toast["type"]) => void }>({
  show: () => {},
});
export function useToast() {
  return useContext(ToastCtx);
}

function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const show = useCallback((msg: string, type: Toast["type"] = "info") => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, msg, type }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3000);
  }, []);
  return (
    <ToastCtx.Provider value={{ show }}>
      {children}
      <div className="toast-container">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.type}`}>{t.msg}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

const NAV: [string, string][] = [
  ["my_dashboard", "/dashboard"],
  ["board", "/board"],
  ["intake", "/intake"],
  ["import_", "/import"],
  ["escalations", "/escalations"],
  ["ward_report", "/ward-report"],
  ["district", "/district"],
  ["demo", "/demo"],
];

function Shell({ children }: { children: ReactNode }) {
  const { user, setUser } = useAuth();
  const { lang, toggle: toggleLang } = useLang();
  const nav = useNavigate();
  const [sseOk, setSseOk] = useState(true);
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    if (typeof window === "undefined") return "dark";
    const saved = localStorage.getItem("ab.theme");
    if (saved === "light" || saved === "dark") return saved;
    return "dark";
  });
  useEffect(() => {
    document.documentElement.classList.toggle("light", theme === "light");
    localStorage.setItem("ab.theme", theme);
  }, [theme]);

  // ── T5: global patient search ───────────────────────────────────────────
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQ, setSearchQ] = useState("");
  const [searchResults, setSearchResults] = useState<{id: string; name: string; phone: string; age: number | null}[]>([]);
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!searchOpen) return;
    const t = setTimeout(() => {
      const q = searchQ.trim();
      if (q.length < 1) { setSearchResults([]); return; }
      api<{id: string; name: string; phone: string; age: number | null}[]>(
        `/api/patients/search?q=${encodeURIComponent(q)}`
      ).then(setSearchResults).catch(() => setSearchResults([]));
    }, 200);
    return () => clearTimeout(t);
  }, [searchQ, searchOpen]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        setSearchOpen(true);
        setTimeout(() => searchRef.current?.focus(), 50);
      } else if (e.key === "Escape" && searchOpen) {
        setSearchOpen(false);
        setSearchQ("");
        setSearchResults([]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [searchOpen]);

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
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: "1rem", letterSpacing: "-0.01em", marginBottom: 2, color: C.text }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={C.accent} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>
            </svg>
            AAROGYA BANDHU
          </div>
          <div style={{ color: C.muted, fontSize: "0.75rem", marginBottom: 16 }}>
            {user?.hospital_name}
          </div>

          {/* T5: global patient search */}
          <button
            onClick={() => { setSearchOpen(true); setTimeout(() => searchRef.current?.focus(), 50); }}
            style={{
              display: "block", width: "100%", marginBottom: 12,
              padding: "6px 10px", textAlign: "left",
              background: C.elevated, color: C.muted,
              border: `1px solid ${C.border}`, borderRadius: 4,
              fontFamily: "inherit", fontSize: "0.75rem", cursor: "pointer",
            }}
            title="Search patients (Ctrl+K)"
          >
            [ search patients... Ctrl+K ]
          </button>

          {NAV.map(([key, to]) => (
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
                <>{isActive ? "▸ " : "  "}{t(key)}</>
              )}
            </NavLink>
          ))}
          {user?.role === "admin" && (
            <NavLink to="/staff"
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
                <>{isActive ? "▸ " : "  "}{t("staff_management")}</>
              )}
            </NavLink>
          )}
          {user?.role === "admin" && (
            <NavLink to="/twilio"
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
              title="Live status of every configured Twilio account"
            >
              {({ isActive }) => (
                <>{isActive ? "▸ " : "  "}Twilio Accounts</>
              )}
            </NavLink>
          )}

          <div style={{ marginTop: "auto", paddingTop: 16, borderTop: `1px solid ${C.borderMuted}` }}>
            {/* Theme + language toggles */}
            <Button
              variant="ghost"
              style={{ width: "100%", padding: "6px 10px", marginBottom: 6, fontSize: "0.75rem" }}
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              title="Toggle dark / light theme"
            >
              {theme === "dark" ? "[ light theme ]" : "[ dark theme ]"}
            </Button>
            <Button
              variant="ghost"
              style={{ width: "100%", padding: "6px 10px", marginBottom: 8, fontSize: "0.75rem" }}
              onClick={toggleLang}
            >
              {lang === "en" ? "[ ಕನ್ನಡ ]" : "[ English ]"}
            </Button>
            <div style={{ color: C.muted, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.1em" }}>
              {t("signed_in")}
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
              {t("logout")}
            </Button>
            {/* Keyboard hint */}
            <div style={{ color: C.disabled, fontSize: "0.625rem", marginTop: 8, lineHeight: 1.4 }}>
              {t("keyboard_hint")}
            </div>
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
        {sseOk ? t("connected") : t("disconnected")}
      </footer>

      {/* T5: global patient search modal */}
      {searchOpen && (
        <div
          onClick={() => { setSearchOpen(false); setSearchQ(""); setSearchResults([]); }}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "flex-start", justifyContent: "center", paddingTop: 80, zIndex: 1000 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, width: 480, maxWidth: "92vw", padding: 16, boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}
          >
            <input
              ref={searchRef}
              type="text"
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
              placeholder="search by name or phone..."
              style={{ width: "100%", padding: 10, fontFamily: "inherit", fontSize: "0.875rem", background: C.bg, color: C.text, border: `1px solid ${C.border}`, borderRadius: 4, outline: "none" }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && searchResults[0]) {
                  nav(`/patients/${searchResults[0].id}`);
                  setSearchOpen(false); setSearchQ(""); setSearchResults([]);
                }
              }}
            />
            <div style={{ marginTop: 8, maxHeight: 320, overflow: "auto" }}>
              {searchResults.length === 0 ? (
                <div style={{ color: C.muted, fontSize: "0.75rem", padding: 12, textAlign: "center" }}>
                  {searchQ.trim().length === 0 ? "type a name or phone number" : "no matches"}
                </div>
              ) : (
                searchResults.slice(0, 8).map((p) => (
                  <div
                    key={p.id}
                    onClick={() => { nav(`/patients/${p.id}`); setSearchOpen(false); setSearchQ(""); setSearchResults([]); }}
                    style={{ padding: "8px 10px", cursor: "pointer", borderBottom: `1px solid ${C.borderMuted}`, fontSize: "0.8125rem" }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = C.elevated; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "transparent"; }}
                  >
                    <div style={{ fontWeight: 600 }}>{p.name}</div>
                    <div style={{ color: C.muted, fontSize: "0.75rem" }}>{p.phone}{p.age != null ? ` · age ${p.age}` : ""}</div>
                  </div>
                ))
              )}
            </div>
            <div style={{ marginTop: 8, color: C.disabled, fontSize: "0.625rem", textAlign: "right" }}>
              Enter to open · Esc to close · Ctrl+K to open
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Protected({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div style={{ padding: 24, color: C.muted }} className="loading-pulse">{t("loading")}</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <Shell>{children}</Shell>;
}

function LangWrapper({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(getLang);
  const toggle = useCallback(() => {
    setLangState((prev) => {
      const next = prev === "en" ? "kn" : "en";
      setLang(next);
      return next;
    });
  }, []);
  // re-render on external lang change
  useEffect(() => onLangChange(() => setLangState(getLang())), []);
  return <LangCtx.Provider value={{ lang, toggle }}>{children}</LangCtx.Provider>;
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
      <LangWrapper>
        <ToastProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/" element={<Navigate to="/board" replace />} />
              <Route path="/board" element={<Protected><Board /></Protected>} />
              <Route path="/dashboard" element={<Protected><StaffDashboard /></Protected>} />
              <Route path="/ward-report" element={<Protected><WardReport /></Protected>} />
              <Route path="/district" element={<Protected><DistrictDashboard /></Protected>} />
              <Route path="/intake" element={<Protected><Intake /></Protected>} />
              <Route path="/import" element={<Protected><Import /></Protected>} />
              <Route path="/escalations" element={<Protected><Escalations /></Protected>} />
              <Route path="/demo" element={<Protected><Demo /></Protected>} />
              <Route path="/twilio" element={<Protected><TwilioHealthPage /></Protected>} />
              <Route path="/patients/:id" element={<Protected><PatientDetail /></Protected>} />
              <Route path="/sheet/:eid" element={<Protected><Sheet /></Protected>} />
              <Route path="/print/patient/:id" element={<Protected><PrintPatient /></Protected>} />
              <Route path="/staff" element={<Protected><Staff /></Protected>} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
        </ToastProvider>
      </LangWrapper>
    </AuthCtx.Provider>
  );
}
