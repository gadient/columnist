// Auth API (BFF) — thin typed wrappers over the backend /auth/* endpoints.
// The browser never holds a raw token: credentials ride in an httpOnly cookie set by the
// backend (see api/client.ts `credentials:'include'`), and the CSRF header is auto-attached
// on unsafe methods. So these functions just shape requests/responses; there is no token here.
import { apiRequest } from './client';

export interface AuthConfig {
  enabled: boolean;
  csrfHeader?: string;
  csrfCookie?: string;
}

export type MemberRole = 'piu' | 'siu';

export interface MeUser {
  id: string;
  email: string;
  name?: string;
  instanceId?: string;
  role?: MemberRole;
}

export interface MeResponse {
  authenticated: boolean;
  user: MeUser | null;
}

// Login can succeed (cookie set) or demand a first-login password change.
export type LoginResult =
  | { status: 'OK' }
  | { status: 'NEW_PASSWORD_REQUIRED'; session: string; email: string };

export interface InstanceMember {
  id: string;
  email: string;
  role: MemberRole;
  status: string;
}

export const apiAuthConfig = () => apiRequest('/auth/config') as Promise<AuthConfig>;

export const apiMe = () => apiRequest('/auth/me') as Promise<MeResponse>;

export const apiLogin = (email: string, password: string) =>
  apiRequest('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }) as Promise<LoginResult>;

export const apiSetPassword = (email: string, newPassword: string, session: string) =>
  apiRequest('/auth/set-password', {
    method: 'POST',
    body: JSON.stringify({ email, new_password: newPassword, session })
  }) as Promise<{ status: string }>;

export const apiForgotPassword = (email: string) =>
  apiRequest('/auth/forgot-password', { method: 'POST', body: JSON.stringify({ email }) }) as Promise<{ status: string }>;

export const apiConfirmForgotPassword = (email: string, code: string, newPassword: string) =>
  apiRequest('/auth/confirm-forgot-password', {
    method: 'POST',
    body: JSON.stringify({ email, code, new_password: newPassword })
  }) as Promise<{ status: string }>;

/** Change your own password while signed in. The account comes from the session, not the body. */
export const apiChangePassword = (currentPassword: string, newPassword: string) =>
  apiRequest('/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
  }) as Promise<{ status: string }>;

export const apiLogout = () => apiRequest('/auth/logout', { method: 'POST' }) as Promise<{ status: string }>;

// ── Instance membership (PIU manages their members) ──
export const apiListMembers = (instanceId: string) =>
  apiRequest(`/instances/${instanceId}/members`) as Promise<InstanceMember[]>;

export const apiInviteMember = (instanceId: string, email: string) =>
  apiRequest(`/instances/${instanceId}/invites`, { method: 'POST', body: JSON.stringify({ email }) }) as Promise<InstanceMember>;

export const apiRemoveMember = (instanceId: string, memberId: string) =>
  apiRequest(`/instances/${instanceId}/members/${memberId}`, { method: 'DELETE' }) as Promise<null>;
