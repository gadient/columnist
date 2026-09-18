// Account control for the global nav (BFF auth). Renders nothing when auth is off (local dev)
// or while unauthenticated. Shows the signed-in email, a sign-out action, and — for a Primary
// user (PIU) — a "Manage members" modal to invite/remove their secondary users (SIUs).
import React, { useEffect, useRef, useState } from 'react';
import { LogOut, Users, ChevronDown, X, Trash2, Loader2, UserPlus, KeyRound, CheckCircle2 } from 'lucide-react';
import { useAuth } from '../../auth/AuthContext';
import { apiInviteMember, apiListMembers, apiRemoveMember, type InstanceMember } from '../../api/auth';

export function AccountMenu() {
  const { authEnabled, status, user, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [showMembers, setShowMembers] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  if (!authEnabled || status !== 'authenticated' || !user) return null;
  const isPiu = user.role === 'piu';

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: '0.375rem', padding: '0.375rem 0.625rem',
          backgroundColor: '#f3f4f6', color: '#374151', fontSize: '0.8125rem', fontWeight: 600,
          borderRadius: '0.5rem', border: '1px solid #d1d5db', cursor: 'pointer', maxWidth: '14rem'
        }}
      >
        <span style={{ width: 22, height: 22, borderRadius: '9999px', background: 'linear-gradient(135deg,#f59e0b,#d97706)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', fontWeight: 700 }}>
          {(user.email || '?').charAt(0).toUpperCase()}
        </span>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{user.email}</span>
        <ChevronDown size={14} />
      </button>

      {open && (
        <div style={{ position: 'absolute', right: 0, top: 'calc(100% + 6px)', minWidth: '11rem', backgroundColor: '#fff', border: '1px solid #e5e7eb', borderRadius: '0.625rem', boxShadow: '0 8px 24px rgba(0,0,0,0.12)', overflow: 'hidden', zIndex: 10000 }}>
          {isPiu && (
            <button onClick={() => { setOpen(false); setShowMembers(true); }} style={menuItem}>
              <Users size={15} /> Manage members
            </button>
          )}
          <button onClick={() => { setOpen(false); setShowPassword(true); }} style={menuItem}>
            <KeyRound size={15} /> Change password
          </button>
          <button onClick={() => { setOpen(false); signOut(); }} style={{ ...menuItem, color: '#b91c1c' }}>
            <LogOut size={15} /> Sign out
          </button>
        </div>
      )}

      {showMembers && user.instanceId && (
        <MembersModal instanceId={user.instanceId} onClose={() => setShowMembers(false)} />
      )}

      {showPassword && <ChangePasswordModal onClose={() => setShowPassword(false)} />}
    </div>
  );
}

const menuItem: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '0.5rem', width: '100%', padding: '0.5rem 0.75rem',
  fontSize: '0.8125rem', fontWeight: 500, color: '#374151', background: 'none', border: 'none',
  cursor: 'pointer', textAlign: 'left'
};

function MembersModal({ instanceId, onClose }: { instanceId: string; onClose: () => void }) {
  const [members, setMembers] = useState<InstanceMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setMembers(await apiListMembers(instanceId));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load members');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instanceId]);

  const invite = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiInviteMember(instanceId, email.trim());
      setEmail('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send invite');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (memberId: string) => {
    setError(null);
    try {
      await apiRemoveMember(instanceId, memberId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove member');
    }
  };

  return (
    <div
      onClick={onClose}
      style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(15,23,42,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10001, padding: '1rem' }}
    >
      <div onClick={(e) => e.stopPropagation()} style={{ width: '100%', maxWidth: '30rem', backgroundColor: '#fff', borderRadius: '0.875rem', boxShadow: '0 20px 60px rgba(0,0,0,0.25)', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '1rem 1.25rem', borderBottom: '1px solid #f1f5f9' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Users size={18} color="#d97706" />
            <h2 style={{ fontSize: '1rem', fontWeight: 700, color: '#0f172a', margin: 0 }}>Members</h2>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#94a3b8' }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: '1rem 1.25rem' }}>
          <p style={{ fontSize: '0.8125rem', color: '#64748b', marginTop: 0 }}>
            Invite people into your space. They’ll get an email with a temporary password.
          </p>

          <form onSubmit={invite} style={{ display: 'flex', gap: '0.5rem', margin: '0.75rem 0 1rem' }}>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@example.com"
              style={{ flex: 1, padding: '0.5rem 0.75rem', fontSize: '0.8125rem', border: '1px solid #cbd5e1', borderRadius: '0.5rem', outline: 'none' }}
            />
            <button
              type="submit"
              disabled={busy}
              style={{ display: 'flex', alignItems: 'center', gap: '0.375rem', padding: '0.5rem 0.875rem', fontSize: '0.8125rem', fontWeight: 600, color: '#fff', background: 'linear-gradient(135deg,#f59e0b,#d97706)', border: 'none', borderRadius: '0.5rem', cursor: 'pointer', opacity: busy ? 0.7 : 1 }}
            >
              {busy ? <Loader2 size={15} className="animate-spin" /> : <UserPlus size={15} />}
              Invite
            </button>
          </form>

          {error && (
            <div style={{ marginBottom: '0.75rem', padding: '0.5rem 0.75rem', fontSize: '0.75rem', color: '#b91c1c', backgroundColor: '#fef2f2', border: '1px solid #fecaca', borderRadius: '0.5rem' }}>
              {error}
            </div>
          )}

          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: '#94a3b8' }}>
              <Loader2 className="animate-spin" />
            </div>
          ) : members.length === 0 ? (
            <p style={{ fontSize: '0.8125rem', color: '#94a3b8', textAlign: 'center', padding: '1rem 0' }}>No members yet — invite someone above.</p>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
              {members.map((m) => (
                <li key={m.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.5rem 0.75rem', backgroundColor: '#f8fafc', borderRadius: '0.5rem' }}>
                  <div style={{ overflow: 'hidden' }}>
                    <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: '#0f172a', overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.email}</div>
                    <div style={{ fontSize: '0.6875rem', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                      {m.role === 'piu' ? 'Owner' : 'Member'} · {m.status}
                    </div>
                  </div>
                  {m.role !== 'piu' && (
                    <button onClick={() => remove(m.id)} aria-label={`Remove ${m.email}`} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#ef4444', padding: '0.25rem' }}>
                      <Trash2 size={15} />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// Change your password while signed in. Distinct from the login page's "Forgot password?" (for
// people who *cannot* sign in) and from the first-login set-password challenge.
//
// The current password is required: the backend re-authenticates with it, so a hijacked session
// alone cannot take over the account. Client-side validation stays deliberately thin — length and
// the confirm match only. The real policy lives on the Cognito pool, and its rejection message is
// passed through verbatim rather than duplicated here, where it would drift.
const MIN_PASSWORD = 12;

function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const { changePassword } = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (next.length < MIN_PASSWORD) return setError(`New password must be at least ${MIN_PASSWORD} characters.`);
    if (next !== confirm) return setError('New passwords do not match.');
    if (next === current) return setError('The new password must be different from the current one.');
    setBusy(true);
    try {
      await changePassword(current, next);
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change password');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      onClick={onClose}
      style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(15,23,42,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10001, padding: '1rem' }}
    >
      <div onClick={(e) => e.stopPropagation()} style={{ width: '100%', maxWidth: '26rem', backgroundColor: '#fff', borderRadius: '0.875rem', boxShadow: '0 20px 60px rgba(0,0,0,0.25)', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '1rem 1.25rem', borderBottom: '1px solid #f1f5f9' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <KeyRound size={18} color="#d97706" />
            <h2 style={{ fontSize: '1rem', fontWeight: 700, color: '#0f172a', margin: 0 }}>Change password</h2>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#94a3b8' }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: '1rem 1.25rem' }}>
          {done ? (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.625rem 0.75rem', fontSize: '0.8125rem', color: '#166534', backgroundColor: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: '0.5rem' }}>
                <CheckCircle2 size={16} />
                Password changed. You’re still signed in.
              </div>
              <button onClick={onClose} style={{ marginTop: '0.875rem', width: '100%', padding: '0.5rem', fontSize: '0.8125rem', fontWeight: 600, color: '#fff', background: 'linear-gradient(135deg,#f59e0b,#d97706)', border: 'none', borderRadius: '0.5rem', cursor: 'pointer' }}>
                Done
              </button>
            </>
          ) : (
            <form onSubmit={submit}>
              <PwField label="Current password" value={current} onChange={setCurrent} autoFocus />
              <PwField label="New password" value={next} onChange={setNext} />
              <PwField label="Confirm new password" value={confirm} onChange={setConfirm} />

              {error && (
                <div style={{ marginTop: '0.75rem', padding: '0.5rem 0.75rem', fontSize: '0.75rem', color: '#b91c1c', backgroundColor: '#fef2f2', border: '1px solid #fecaca', borderRadius: '0.5rem' }}>
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={busy}
                style={{ marginTop: '0.875rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.375rem', width: '100%', padding: '0.5rem', fontSize: '0.8125rem', fontWeight: 600, color: '#fff', background: 'linear-gradient(135deg,#f59e0b,#d97706)', border: 'none', borderRadius: '0.5rem', cursor: 'pointer', opacity: busy ? 0.7 : 1 }}
              >
                {busy && <Loader2 size={15} className="animate-spin" />}
                Change password
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

function PwField({
  label, value, onChange, autoFocus
}: { label: string; value: string; onChange: (v: string) => void; autoFocus?: boolean }) {
  return (
    <label style={{ display: 'block', marginBottom: '0.625rem' }}>
      <span style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, color: '#475569', marginBottom: '0.25rem' }}>{label}</span>
      <input
        type="password"
        required
        autoFocus={autoFocus}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ width: '100%', padding: '0.5rem 0.75rem', fontSize: '0.8125rem', border: '1px solid #cbd5e1', borderRadius: '0.5rem', outline: 'none' }}
      />
    </label>
  );
}
