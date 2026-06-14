import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { getAuthConfig } from "../api";
import { homeForRole, useAuth } from "../auth-context";
import { useTheme } from "../theme-context";

// Wayfinding-only roles for the optional login toggle. Selecting one switches the
// username placeholder + a short description; it never pre-fills a value or password,
// and it does NOT change auth — the real role always comes from the credentials.
const ROLE_HELP = {
  ops: { label: "Operator", placeholder: "ops", blurb: "Full ops console — every claim, eval runs, fraud review, policy studio." },
  member: { label: "Member", placeholder: "e.g. EMP001", blurb: "A member's own claims and new submissions." },
} as const;
type RoleKey = keyof typeof ROLE_HELP;

// Deterministic starfield (no RNG, so it renders identically every time) for the login
// art panel. Entirely original CSS/SVG — evokes Plum's cosmic brand without using any of
// their proprietary artwork.
const STARS = Array.from({ length: 54 }, (_, i) => ({
  cx: (i * 97 + 13) % 600,
  cy: (i * 57 + 29) % 760,
  r: i % 5 === 0 ? 1.5 : i % 3 === 0 ? 1 : 0.6,
  o: 0.25 + ((i * 7) % 12) / 16,
}));

function CosmicBackdrop() {
  return (
    <svg className="absolute inset-0 h-full w-full" viewBox="0 0 600 760"
         preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <defs>
        <linearGradient id="lp-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#2c0b21" />
          <stop offset="0.5" stopColor="#1d0716" />
          <stop offset="1" stopColor="#11040d" />
        </linearGradient>
        <radialGradient id="lp-aura1">
          <stop offset="0" stopColor="#ff4052" stopOpacity="0.33" />
          <stop offset="1" stopColor="#ff4052" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="lp-aura2">
          <stop offset="0" stopColor="#7e14ff" stopOpacity="0.30" />
          <stop offset="1" stopColor="#7e14ff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="600" height="760" fill="url(#lp-sky)" />
      <ellipse cx="300" cy="610" rx="300" ry="230" fill="url(#lp-aura1)" />
      <ellipse cx="120" cy="210" rx="240" ry="220" fill="url(#lp-aura2)" />
      {STARS.map((s, i) => (
        <circle key={i} cx={s.cx} cy={s.cy} r={s.r} fill="#fff8f2" opacity={s.o} />
      ))}
    </svg>
  );
}

// Login page (route /login). Only reachable/required when the backend reports
// auth_enabled === true. On success it routes to the role's home. When auth is
// OFF, App.tsx never mounts this and an /login visit is redirected away.
export default function Login() {
  const { enabled, user, login } = useAuth();
  const { theme, toggle } = useTheme();
  const toDark = theme === "light";
  const navigate = useNavigate();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Optional Operator|Member wayfinding toggle, gated by the backend SHOW_ROLE_HELP flag.
  const [roleHelp, setRoleHelp] = useState(false);
  const [role, setRole] = useState<RoleKey>("ops");
  useEffect(() => {
    let active = true;
    getAuthConfig()
      .then((c) => active && setRoleHelp(Boolean(c.show_role_help)))
      .catch(() => {}); // help is non-essential — silently skip if the probe fails
    return () => {
      active = false;
    };
  }, []);

  // Auth off, or already logged in → don't show the wall; bounce to a home.
  useEffect(() => {
    if (!enabled) navigate("/", { replace: true });
    else if (user) navigate(homeForRole(user.role), { replace: true });
  }, [enabled, user, navigate]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const u = await login(username.trim(), password);
      navigate(homeForRole(u.role), { replace: true });
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Could not sign in. Please try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  const inputClass =
    "w-full rounded-xl border border-plum-800/15 bg-white px-3.5 py-2.5 text-sm text-plum-800 outline-none transition-colors placeholder:text-plum-800/30 focus:border-coral focus:ring-2 focus:ring-coral/20 dark:border-creamtext/15 dark:bg-plum-700 dark:text-creamtext dark:placeholder:text-creamtext/30";

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Theme toggle — the app nav is hidden on /login, so the login carries its own.
          Theme-aware colours so it reads on the cream form (light) and on dark. */}
      <button
        type="button"
        onClick={toggle}
        aria-label={toDark ? "Switch to dark mode" : "Switch to light mode"}
        title={toDark ? "Switch to dark mode" : "Switch to light mode"}
        className="absolute right-4 top-4 z-20 flex h-9 w-9 items-center justify-center rounded-full text-plum-800/55 transition-colors hover:bg-plum-800/10 hover:text-plum-800 dark:text-creamtext/70 dark:hover:bg-creamtext/10 dark:hover:text-creamtext"
      >
        {toDark ? (
          <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M21.752 15.002A9.718 9.718 0 0 1 18 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 0 0 3 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 0 0 9.002-5.998Z" />
          </svg>
        ) : (
          <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v2.25m6.364.386-1.591 1.591M21 12h-2.25m-.386 6.364-1.591-1.591M12 18.75V21m-4.773-4.227-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0Z" />
          </svg>
        )}
      </button>
      {/* Art panel (left) — original cosmic-plum visual; decorative + desktop-only. */}
      <aside
        aria-hidden="true"
        className="relative hidden overflow-hidden bg-plum-900 p-12 lg:flex lg:flex-col lg:justify-between xl:p-16"
      >
        <CosmicBackdrop />
        <span className="relative z-10 font-serif text-2xl lowercase tracking-tight text-creamtext">
          <span className="text-coral">plum</span> claims
        </span>
        <div className="relative z-10">
          <h2 className="font-serif text-5xl leading-[1.05] text-creamtext">
            Claims,
            <br />
            handled <span className="text-coral">with care.</span>
          </h2>
          <p className="mt-5 max-w-sm text-sm leading-relaxed text-creamtext/55">
            AI-assisted adjudication — document verification, source-bound extraction, and an
            explainable decision for every claim.
          </p>
        </div>
        <p className="relative z-10 text-xs text-creamtext/40">Made with ♥ in India</p>
      </aside>

      {/* Form panel (right). */}
      <div className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-md">
      <header className="mb-8 text-center">
        {/* Wordmark only on mobile — on desktop the art panel already carries it. */}
        <p className="mb-6 font-serif text-3xl lowercase tracking-tight lg:hidden">
          <span className="text-coral">plum</span>
          <span className="text-plum-800 dark:text-creamtext"> claims</span>
        </p>
        <h1 className="font-serif text-3xl text-plum-800 dark:text-creamtext">
          Sign in
        </h1>
        <p className="mt-2 text-sm text-plum-800/60 dark:text-creamtext/60">
          Sign in to access your claims portal.
        </p>
      </header>

      <form
        onSubmit={handleSubmit}
        className="rounded-card border border-plum-800/[0.12] bg-white p-7 shadow-sm dark:border-creamtext/10 dark:bg-plum-800"
        aria-describedby={error ? "login-error" : undefined}
      >
        <div className="flex flex-col gap-5">
          {roleHelp && (
            <div>
              {/* Wayfinding only — picks the experience to sign into. Does not change
                  auth or pre-fill anything; the role comes from your credentials. */}
              <div role="group" aria-label="Choose role to sign in as"
                   className="grid grid-cols-2 gap-1 rounded-full bg-plum-800/[0.06] p-1 dark:bg-creamtext/10">
                {(Object.keys(ROLE_HELP) as RoleKey[]).map((key) => {
                  const active = role === key;
                  return (
                    <button
                      key={key}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setRole(key)}
                      className={[
                        "rounded-full px-3 py-1.5 text-sm font-medium transition-colors",
                        active
                          ? "bg-coral text-white shadow-sm"
                          : "text-plum-800/70 hover:text-plum-800 dark:text-creamtext/70 dark:hover:text-creamtext",
                      ].join(" ")}
                    >
                      {ROLE_HELP[key].label}
                    </button>
                  );
                })}
              </div>
              <p className="mt-2 text-center text-xs leading-relaxed text-plum-800/55 dark:text-creamtext/55">
                {ROLE_HELP[role].blurb}
              </p>
            </div>
          )}
          <div>
            <label
              htmlFor="login-username"
              className="mb-1.5 block text-sm font-medium text-plum-800 dark:text-creamtext"
            >
              Username
            </label>
            <input
              id="login-username"
              name="username"
              type="text"
              autoComplete="username"
              autoFocus
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className={inputClass}
              placeholder={roleHelp ? ROLE_HELP[role].placeholder : "ops or EMP001"}
            />
          </div>

          <div>
            <label
              htmlFor="login-password"
              className="mb-1.5 block text-sm font-medium text-plum-800 dark:text-creamtext"
            >
              Password
            </label>
            <input
              id="login-password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={inputClass}
              placeholder="••••••••"
            />
          </div>

          {error && (
            <div
              id="login-error"
              role="alert"
              aria-live="assertive"
              className="rounded-xl border border-crimson/30 bg-crimson/5 px-4 py-3 text-sm text-crimson"
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="inline-flex items-center justify-center gap-2 rounded-full bg-coral px-7 py-3 text-sm font-semibold text-white transition-colors hover:bg-plum-800 disabled:cursor-not-allowed disabled:opacity-60 dark:hover:bg-plum-700"
          >
            {submitting && (
              <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4Z" />
              </svg>
            )}
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </form>

      {/* Dev-only convenience: the seeded default credentials. Vite inlines
          import.meta.env.DEV as false in production builds, so this hint is
          tree-shaken out of the deployed app — a prod login page must never
          advertise credentials (and the real prod passwords differ anyway). */}
      {import.meta.env.DEV && (
        <p className="mt-5 text-center text-xs leading-relaxed text-plum-800/45 dark:text-creamtext/45">
          Dev credentials — ops:{" "}
          <span className="font-mono">ops</span> /{" "}
          <span className="font-mono">ops-dev-password</span> · member:{" "}
          <span className="font-mono">EMP001</span> /{" "}
          <span className="font-mono">member-dev-password</span>
        </p>
      )}
        </div>
      </div>
    </div>
  );
}
