// Dark-mode theme context (Round 5). Toggles the `dark` class on <html> so the
// Tailwind `dark:` variants engage. The choice is persisted in localStorage and,
// on first load (no stored choice), follows the OS `prefers-color-scheme`.
//
// Light remains the default for a fresh visitor on a light-themed OS, so the
// auth-off "byte-for-byte current UX" expectation holds (the toggle just adds an
// affordance; it doesn't flip the default theme).

import { useEffect, useState, type ReactNode } from "react";

import { ThemeContext, type ThemeContextValue } from "./theme-context";

type Theme = "light" | "dark";

const STORAGE_KEY = "plum.theme";

function readInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // localStorage unavailable → fall through to the OS preference.
  }
  try {
    if (window.matchMedia?.("(prefers-color-scheme: dark)").matches) return "dark";
  } catch {
    // matchMedia unavailable → default light.
  }
  return "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(readInitialTheme);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // best-effort persistence
    }
  }, [theme]);

  const value: ThemeContextValue = {
    theme,
    toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
  };

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
