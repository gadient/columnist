// Auth types. Auth is Cognito via a backend-for-frontend, enforced only when Cognito is configured.

// 'offline' = the deployed SPA loaded but the backend never answered /auth/config (service down or
// paused). It is deliberately distinct from 'unauthenticated' so the boot gate can fail CLOSED to a
// maintenance screen instead of falling through to the open no-auth app. See AuthContext bootstrap.
export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated' | 'offline';

export type MemberRole = 'piu' | 'siu';

export interface AuthUser {
  id: string;
  email: string;
  name?: string;
  // Present when Cognito is on: the caller's Instance and their role within it (from /auth/me).
  instanceId?: string;
  role?: MemberRole;
}

export interface AuthState {
  status: AuthStatus;
  user: AuthUser | null;
}
