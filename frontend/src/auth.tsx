// Auth context (Round 5). Single source of truth for whether the login wall +
// role gating are engaged, and who the current principal is.
//
// CRITICAL — auth-off parity: on app load we probe GET /api/auth/config. When
// `auth_enabled` is false (the default), there is NO login and NO gating: the
// context reports a synthetic ops/"system" principal so every nav item + route
// renders exactly as it did before this round. Login + RBAC engage ONLY when the
// backend reports auth_enabled === true.

import { useEffect, useMemo, useState, type ReactNode } from "react";

import {
  clearToken,
  getAuthConfig,
  getMe,
  getToken,
  login as apiLogin,
} from "./api";

import {
  AuthContext,
  SYSTEM_USER,
  toUser,
  type AuthContextValue,
  type AuthUser,
} from "./auth-context";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    (async () => {
      let authEnabled;
      try {
        const cfg = await getAuthConfig();
        authEnabled = cfg.auth_enabled;
      } catch {
        // Config probe failed (e.g. backend booting) → fail OPEN to the current
        // behaviour: treat auth as off so the app never gets stuck behind a wall.
        authEnabled = false;
      }
      if (!active) return;
      setEnabled(authEnabled);

      if (!authEnabled) {
        // Auth off → synthetic ops principal; the app renders exactly as today.
        setUser(SYSTEM_USER);
        setLoading(false);
        return;
      }

      // Auth on → if we already hold a token, resolve the principal from /me.
      if (getToken()) {
        try {
          const me = await getMe();
          if (active) setUser(toUser(me));
        } catch {
          // Stale / invalid token → drop it and require a fresh login.
          clearToken();
          if (active) setUser(null);
        }
      }
      if (active) setLoading(false);
    })();
    return () => {
      active = false;
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      enabled,
      user,
      loading,
      async login(username: string, password: string) {
        const res = await apiLogin(username, password);
        const u: AuthUser = {
          username,
          role: res.role,
          member_id: res.member_id,
        };
        setUser(u);
        return u;
      },
      logout() {
        clearToken();
        setUser(null);
      },
    }),
    [enabled, user, loading]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
