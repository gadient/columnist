// Theme system. Three preferences — 'light' | 'dark' | 'system' —
// persisted to localStorage under `columnist_theme`. The resolved theme (system → the
// OS preference at runtime) is written to <html data-theme="…">, which flips every
// semantic token defined in index.css / tailwind.config.js.
//
// Default is 'light': a consistent bright product out of the box, with dark one
// toggle away. Change DEFAULT_PREFERENCE to 'system' to follow the OS instead.
import React, { createContext, useContext, useEffect, useMemo, useState, useCallback } from 'react';

export type ThemePreference = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

const STORAGE_KEY = 'columnist_theme';
const DEFAULT_PREFERENCE: ThemePreference = 'light';

interface ThemeContextValue {
  /** The user's stored choice. */
  preference: ThemePreference;
  /** What is actually applied right now (system resolved to light/dark). */
  resolved: ResolvedTheme;
  setPreference: (pref: ThemePreference) => void;
  /** Convenience: cycles light → dark → system. */
  cycle: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readStoredPreference(): ThemePreference {
  if (typeof window === 'undefined') return DEFAULT_PREFERENCE;
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  return DEFAULT_PREFERENCE;
}

function systemPrefersDark(): boolean {
  return typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-color-scheme: dark)').matches === true;
}

function resolve(pref: ThemePreference): ResolvedTheme {
  if (pref === 'system') return systemPrefersDark() ? 'dark' : 'light';
  return pref;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference);
  const [resolved, setResolved] = useState<ResolvedTheme>(() => resolve(readStoredPreference()));

  // Apply the resolved theme to <html> and keep it in sync with preference + OS changes.
  useEffect(() => {
    const apply = () => {
      const next = resolve(preference);
      setResolved(next);
      document.documentElement.setAttribute('data-theme', next);
    };
    apply();

    if (preference !== 'system') return;
    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    mql.addEventListener('change', apply);
    return () => mql.removeEventListener('change', apply);
  }, [preference]);

  const setPreference = useCallback((pref: ThemePreference) => {
    window.localStorage.setItem(STORAGE_KEY, pref);
    setPreferenceState(pref);
  }, []);

  const cycle = useCallback(() => {
    setPreferenceState((prev) => {
      const next: ThemePreference = prev === 'light' ? 'dark' : prev === 'dark' ? 'system' : 'light';
      window.localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({ preference, resolved, setPreference, cycle }),
    [preference, resolved, setPreference, cycle],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within a ThemeProvider');
  return ctx;
}
