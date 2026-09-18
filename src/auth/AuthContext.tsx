// Auth context (BFF / Cognito). No tokens live in JS — the backend sets an httpOnly session
// cookie at login and this provider only ever talks to /auth/*. On mount it reads /auth/config;
// if auth is off (local dev) the app stays open and unauthenticated. If on, it hydrates from
// /auth/me.
import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import type { AuthState } from './types';
import {
  apiAuthConfig,
  apiChangePassword,
  apiConfirmForgotPassword,
  apiForgotPassword,
  apiLogin,
  apiLogout,
  apiMe,
  apiSetPassword,
  type LoginResult
} from '../api/auth';

interface AuthContextValue extends AuthState {
  authEnabled: boolean;
  authError: string | null;
  login: (email: string, password: string) => Promise<LoginResult>;
  setPassword: (email: string, newPassword: string, session: string) => Promise<void>;
  forgotPassword: (email: string) => Promise<void>;
  confirmForgotPassword: (email: string, code: string, newPassword: string) => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const [state, setState] = useState<AuthState>({ status: 'loading', user: null });
  const [authEnabled, setAuthEnabled] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const me = await apiMe();
      if (me.authenticated && me.user) {
        setAuthError(null);
        setState({ status: 'authenticated', user: me.user });
      } else {
        setState({ status: 'unauthenticated', user: null });
      }
    } catch (err) {
      // A valid session with no invite yields 403 ("not invited") — surface it so the login
      // screen can explain, rather than silently looping the user back to the form.
      //
      // A 401 is different and must NOT be surfaced: it is what a stale or expired session cookie
      // produces on boot, and "not signed in" is the normal state of the sign-in page — which
      // already says so. Showing the backend's "Invalid token" to someone who has not typed
      // anything reads as a broken app, and returning users hit it on every visit after the 12h
      // cookie TTL lapses.
      const status = (err as { status?: number } | null)?.status;
      setAuthError(status === 401 ? null : err instanceof Error ? err.message : 'Sign-in failed');
      setState({ status: 'unauthenticated', user: null });
    }
  }, []);

  // Bootstrap: learn whether auth is on, and if so hydrate the session.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let enabled = false;
      try {
        enabled = (await apiAuthConfig()).enabled;
      } catch {
        if (cancelled) return;
        // The backend didn't answer /auth/config. In a DEPLOYED build this means the service is
        // down or paused (the SPA is static and a CDN keeps serving it even then). Fail CLOSED to
        // an offline screen — falling through to `enabled=false` here would render the whole app as
        // if Cognito were disabled (the app fails OPEN when paused). In local dev (no backend up)
        // keep the permissive fall-through so a frontend-only session still boots.
        if (import.meta.env.PROD) {
          setState({ status: 'offline', user: null });
          return;
        }
        enabled = false;
      }
      if (cancelled) return;
      setAuthEnabled(enabled);
      if (enabled) {
        await refresh();
      } else {
        // Auth off (local dev): app is open. Report unauthenticated so gates fall through.
        setState({ status: 'unauthenticated', user: null });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const login = useCallback(
    async (email: string, password: string): Promise<LoginResult> => {
      setAuthError(null);
      const result = await apiLogin(email, password);
      if (result.status === 'OK') await refresh();
      return result;
    },
    [refresh]
  );

  const setPassword = useCallback(
    async (email: string, newPassword: string, session: string) => {
      await apiSetPassword(email, newPassword, session);
      await refresh();
    },
    [refresh]
  );

  const forgotPassword = useCallback(async (email: string) => {
    await apiForgotPassword(email);
  }, []);

  const confirmForgotPassword = useCallback(async (email: string, code: string, newPassword: string) => {
    await apiConfirmForgotPassword(email, code, newPassword);
  }, []);

  // No `refresh()` afterwards: Cognito leaves existing tokens valid after a password change, so
  // the session survives and there is nothing to re-hydrate.
  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    await apiChangePassword(currentPassword, newPassword);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setState({ status: 'unauthenticated', user: null });
    }
  }, []);

  return (
    <AuthContext.Provider
      value={{
        ...state,
        authEnabled,
        authError,
        login,
        setPassword,
        forgotPassword,
        confirmForgotPassword,
        changePassword,
        signOut,
        refresh
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>');
  return ctx;
}
