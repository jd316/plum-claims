// Auth context (Round 5). Single source of truth for whether the login wall +
// role gating are engaged, and who the current principal is.
//
// CRITICAL — auth-off parity: on app load we probe GET /api/auth/config. When
// `auth_enabled` is false (the default), there is NO login and NO gating: the
// context reports a synthetic ops/"system" principal so every nav item + route
// renders exactly as it did before this round. Login + RBAC engage ONLY when the
// backend reports auth_enabled === true.

import { createContext, useContext } from "react";

import { type MeResponse } from "./api";

export type Role = "member" | "ops";

export interface AuthUser {
  username: string;
  role: Role;
  member_id: string | null;
}

export interface AuthContextValue {
  // True only when the backend has auth turned on. When false the UI is open.
  enabled: boolean;
  // The resolved principal. When auth is OFF this is the synthetic system/ops
  // user so every gate passes. When ON it is the logged-in user, or null if not
  // yet logged in.
  user: AuthUser | null;
  // Still resolving the config probe (and, when enabled + a token exists, /me).
  loading: boolean;
  login: (username: string, password: string) => Promise<AuthUser>;
  logout: () => void;
}

// The synthetic principal used when auth is OFF — ops role so all gates pass.
export const SYSTEM_USER: AuthUser = { username: "system", role: "ops", member_id: null };

export const AuthContext = createContext<AuthContextValue | null>(null);

// The home route for a role — where login / a redirected gate lands you.
export function homeForRole(role: Role): string {
  return role === "ops" ? "/ops" : "/claims";
}

export function toUser(me: MeResponse): AuthUser {
  return { username: me.username, role: me.role, member_id: me.member_id };
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
