import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import Submit from "./pages/Submit";
import Claim from "./pages/Claim";
import Claims from "./pages/Claims";
import Eval from "./pages/Eval";
import OpsDashboard from "./pages/OpsDashboard";
import OpsWorklist from "./pages/OpsWorklist";
import OpsFraud from "./pages/OpsFraud";
import PolicyStudio from "./pages/PolicyStudio";
import Login from "./pages/Login";
import { homeForRole, useAuth, type Role } from "./auth-context";
import { useTheme } from "./theme-context";

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  [
    "text-sm font-medium tracking-wide transition-colors",
    isActive ? "text-coral" : "text-creamtext/80 hover:text-creamtext",
  ].join(" ");

// Theme toggle button — lives in the header. Accessible name reflects the action.
function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const toDark = theme === "light";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={toDark ? "Switch to dark mode" : "Switch to light mode"}
      title={toDark ? "Switch to dark mode" : "Switch to light mode"}
      className="flex h-9 w-9 items-center justify-center rounded-full text-creamtext/80 transition-colors hover:bg-creamtext/10 hover:text-creamtext"
    >
      {toDark ? (
        // moon
        <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="M21.752 15.002A9.718 9.718 0 0 1 18 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 0 0 3 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 0 0 9.002-5.998Z" />
        </svg>
      ) : (
        // sun
        <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v2.25m6.364.386-1.591 1.591M21 12h-2.25m-.386 6.364-1.591-1.591M12 18.75V21m-4.773-4.227-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0Z" />
        </svg>
      )}
    </button>
  );
}

// The nav items each role may see. When auth is OFF we show ALL of them (current
// behaviour). The `end` flag mirrors the existing NavLink usage for "/".
interface NavItem {
  to: string;
  label: string;
  end?: boolean;
  ops?: boolean; // ops-only when auth is on
  member?: boolean; // member-only when auth is on (operators review, they don't submit)
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Submit", end: true, member: true },
  { to: "/claims", label: "Claims" },
  { to: "/ops", label: "Ops", ops: true },
  { to: "/eval", label: "Eval", ops: true },
];

function Header() {
  const { enabled, user, logout } = useAuth();
  // Auth off → show everything (today's behaviour). Auth on → filter by role:
  // ops-only items to ops, member-only items to members, role-agnostic to both.
  const items = NAV_ITEMS.filter((item) => {
    if (!enabled) return true;
    if (item.ops) return user?.role === "ops";
    if (item.member) return user?.role === "member";
    return true;
  });

  return (
    <header className="sticky top-0 z-50 bg-plum-800 text-creamtext dark:bg-plum-900 dark:border-b dark:border-creamtext/10">
      <div className="mx-auto flex h-16 max-w-content items-center justify-between gap-3 px-4 sm:px-6">
        <NavLink to="/" className="flex-shrink-0 font-serif text-xl lowercase tracking-tight sm:text-2xl">
          <span className="text-coral">plum</span>
          <span className="text-creamtext"> claims</span>
        </NavLink>
        <nav className="flex items-center gap-3 sm:gap-8" aria-label="Primary">
          {items.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={navLinkClass}>
              {item.label}
            </NavLink>
          ))}
          <div className="flex items-center gap-2 border-l border-creamtext/15 pl-3 sm:gap-3 sm:pl-5">
            <ThemeToggle />
            {enabled && user && (
              <>
                <span className="hidden text-sm text-creamtext/70 sm:inline">
                  {user.username}
                  <span className="ml-1.5 rounded-full bg-creamtext/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-creamtext/80">
                    {user.role}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={logout}
                  className="rounded-full border border-creamtext/25 px-3.5 py-1.5 text-sm font-medium text-creamtext/85 transition-colors hover:bg-creamtext/10 hover:text-creamtext"
                >
                  Log out
                </button>
              </>
            )}
          </div>
        </nav>
      </div>
    </header>
  );
}

// Route guard for ops-only pages. No-op pass-through when auth is OFF; when ON,
// an unauthenticated user is sent to /login and a member is sent to their home.
function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { enabled, user, loading } = useAuth();
  const location = useLocation();
  if (!enabled) return <>{children}</>; // auth off → open, exactly as today
  if (loading) return null; // wait for /me before deciding
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;
  if (user.role !== role) return <Navigate to={homeForRole(user.role)} replace />;
  return <>{children}</>;
}

// Any logged-in user (member or ops) when auth is on; open when off.
function RequireAuth({ children }: { children: ReactNode }) {
  const { enabled, user, loading } = useAuth();
  const location = useLocation();
  if (!enabled) return <>{children}</>;
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;
  return <>{children}</>;
}

function NotFound() {
  return (
    <div className="mx-auto flex max-w-content flex-col items-center justify-center px-6 py-32 text-center">
      <p className="font-serif text-6xl text-coral">404</p>
      <h1 className="mt-3 font-serif text-4xl text-plum-800 dark:text-creamtext">Page not found</h1>
      <p className="mt-3 max-w-md text-sm text-plum-800/60 dark:text-creamtext/60">
        The page you're looking for doesn't exist or may have moved.
      </p>
      <Link
        to="/"
        className="mt-6 rounded-full bg-coral px-6 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-plum-800"
      >
        Back to Submit
      </Link>
    </div>
  );
}

export default function App() {
  const { loading } = useAuth();
  // The login page is a full-bleed split-screen — no app nav (a logged-out visitor
  // shouldn't see Submit/Claims tabs that only bounce back to /login).
  const onLogin = useLocation().pathname === "/login";

  // While the auth config probe (and /me) resolve, render the chrome but hold
  // the routed content so a guarded route never flashes before we know the role.
  // Gate on `loading` alone (not `enabled && loading`): `enabled` starts false, so
  // gating on it would let the routes render on first paint — flashing the app and
  // firing 401 data calls before the probe resolves and redirects to /login.
  return (
    <div className="min-h-screen bg-cream dark:bg-plum-900">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:rounded-full focus:bg-coral focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-white"
      >
        Skip to content
      </a>
      {!onLogin && <Header />}
      <main id="main-content">
        {loading ? (
          <div className="px-6 py-32 text-center text-sm text-plum-800/50 dark:text-creamtext/50">
            Loading…
          </div>
        ) : (
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/"
              element={
                // Submitting a claim is a member action; an operator who lands here
                // (e.g. via the logo) is sent to their review console. Open when auth off.
                <RequireRole role="member">
                  <Submit />
                </RequireRole>
              }
            />
            <Route
              path="/claims"
              element={
                <RequireAuth>
                  <Claims />
                </RequireAuth>
              }
            />
            <Route
              path="/claims/:id"
              element={
                <RequireAuth>
                  <Claim />
                </RequireAuth>
              }
            />
            <Route
              path="/ops"
              element={
                <RequireRole role="ops">
                  <OpsDashboard />
                </RequireRole>
              }
            />
            <Route
              path="/ops/worklist"
              element={
                <RequireRole role="ops">
                  <OpsWorklist />
                </RequireRole>
              }
            />
            <Route
              path="/ops/fraud"
              element={
                <RequireRole role="ops">
                  <OpsFraud />
                </RequireRole>
              }
            />
            <Route
              path="/ops/policy"
              element={
                <RequireRole role="ops">
                  <PolicyStudio />
                </RequireRole>
              }
            />
            <Route
              path="/eval"
              element={
                <RequireRole role="ops">
                  <Eval />
                </RequireRole>
              }
            />
            <Route path="*" element={<NotFound />} />
          </Routes>
        )}
      </main>
    </div>
  );
}
